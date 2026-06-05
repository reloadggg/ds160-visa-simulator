"use client"

import { FormEvent, useEffect, useMemo, useState } from "react"
import { buildApiUrl } from "@/lib/api/config"
import {
  createAdminAccessKey,
  fetchAdminModelConfigModels,
  getAdminSettings,
  listAdminAccessKeys,
  revealAdminAccessKeySecret,
  testAdminModelConfig,
  updateAdminAccessKey,
  updateAdminSettings,
} from "@/lib/api/client"
import type {
  AdminAccessKeyRecord,
  AdminAccessKeyStatusFilter,
  AdminModelConfigTestResponse,
  AdminSettings,
  ModelListItem,
} from "@/lib/api/types"

type KeySession = {
  session_id: string
  declared_family?: string | null
  phase_state?: string | null
  current_governor_decision?: string | null
  created_at?: string
  message_count: number
}

type AdminMessage = {
  turn_id: string
  turn_index: number
  role: string
  content: string
  source?: string | null
}

const VALIDITY_PRESETS = ["7", "30", "90", "custom", "never"] as const
const EXPIRY_FILTERS = ["all", "active", "expired"] as const
const QUOTA_MODES = ["set", "add"] as const
const ACCESS_KEY_STATUS_FILTERS: AdminAccessKeyStatusFilter[] = [
  "enabled",
  "disabled",
  "all",
]

type ValidityPreset = (typeof VALIDITY_PRESETS)[number]
type ExpiryFilter = (typeof EXPIRY_FILTERS)[number]
type CreateStep = "form" | "confirm"
type ToggleTarget = { key: AdminAccessKeyRecord; nextEnabled: boolean } | null
type QuotaMode = (typeof QUOTA_MODES)[number]
type QuotaTarget = {
  key: AdminAccessKeyRecord
  mode: QuotaMode
  value: number
} | null

type ModelDraft = {
  baseUrl: string
  apiKey: string
  modelName: string
  streamingEnabled: boolean
}

const DEFAULT_MODEL_DRAFT: ModelDraft = {
  baseUrl: "",
  apiKey: "",
  modelName: "",
  streamingEnabled: true,
}

function formatDateTime(
  value?: string | null,
  emptyLabel = "长期有效",
): string {
  if (!value) return emptyLabel
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function addDaysIso(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString()
}

function isExpired(value?: string | null): boolean {
  return Boolean(value && new Date(value).getTime() <= Date.now())
}

function keyStatusLabel(key: AdminAccessKeyRecord): string {
  if (!key.enabled || key.revoked_at) return "已停用"
  if (isExpired(key.expires_at)) return "已过期"
  if ((key.remaining_uses ?? 0) <= 0) return "额度用尽"
  return "可用"
}

function normalizeKeyPreview(key: AdminAccessKeyRecord): string {
  return key.masked_key_preview || `ds160_${key.key_id}_••••`
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

function isAdminAccessKeyStatusFilter(
  value: string,
): value is AdminAccessKeyStatusFilter {
  return ACCESS_KEY_STATUS_FILTERS.some((item) => item === value)
}

function isExpiryFilter(value: string): value is ExpiryFilter {
  return EXPIRY_FILTERS.some((item) => item === value)
}

function isQuotaMode(value: string): value is QuotaMode {
  return QUOTA_MODES.some((item) => item === value)
}

async function adminApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(buildApiUrl(path), {
    credentials: "include",
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  })
  if (!response.ok) {
    throw new Error(`请求失败：${response.status}`)
  }
  return response.json() as Promise<T>
}

export default function AdminPage() {
  const [authenticated, setAuthenticated] = useState(false)
  const [password, setPassword] = useState("")
  const [settings, setSettings] = useState<AdminSettings | null>(null)
  const [keys, setKeys] = useState<AdminAccessKeyRecord[]>([])
  const [query, setQuery] = useState("")
  const [statusFilter, setStatusFilter] =
    useState<AdminAccessKeyStatusFilter>("all")
  const [expiryFilter, setExpiryFilter] = useState<ExpiryFilter>("all")
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [createStep, setCreateStep] = useState<CreateStep>("form")
  const [keyLabel, setKeyLabel] = useState("")
  const [usageLimit, setUsageLimit] = useState(10)
  const [validityPreset, setValidityPreset] = useState<ValidityPreset>("30")
  const [customValidityDays, setCustomValidityDays] = useState(30)
  const [keyEnabled, setKeyEnabled] = useState(true)
  const [revealedKeyId, setRevealedKeyId] = useState<string | null>(null)
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null)
  const [revealDetail, setRevealDetail] = useState<string | null>(null)
  const [toggleTarget, setToggleTarget] = useState<ToggleTarget>(null)
  const [quotaTarget, setQuotaTarget] = useState<QuotaTarget>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [keySessions, setKeySessions] = useState<KeySession[]>([])
  const [selectedMessages, setSelectedMessages] = useState<AdminMessage[]>([])
  const [ragStatus, setRagStatus] = useState<Record<string, unknown> | null>(
    null,
  )
  const [ragFile, setRagFile] = useState<File | null>(null)
  const [modelDraft, setModelDraft] = useState<ModelDraft>(DEFAULT_MODEL_DRAFT)
  const [availableModels, setAvailableModels] = useState<ModelListItem[]>([])
  const [modelSource, setModelSource] = useState<string | null>(null)
  const [modelTestResult, setModelTestResult] =
    useState<AdminModelConfigTestResponse | null>(null)
  const [isFetchingModels, setIsFetchingModels] = useState(false)
  const [isTestingModel, setIsTestingModel] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const selectedKeyRecord = useMemo(
    () => keys.find((key) => key.key_id === selectedKey) ?? null,
    [keys, selectedKey],
  )

  const createExpiresAt = useMemo(() => {
    if (validityPreset === "never") return null
    const days =
      validityPreset === "custom" ? customValidityDays : Number(validityPreset)
    return addDaysIso(Math.max(1, days || 1))
  }, [customValidityDays, validityPreset])

  const expiredParam =
    expiryFilter === "all" ? null : expiryFilter === "expired"

  const refreshKeys = async () => {
    const payload = await listAdminAccessKeys({
      q: query,
      status: statusFilter,
      expired: expiredParam,
    })
    setKeys(payload.keys)
  }

  const refresh = async () => {
    const [settingsPayload, keysPayload, ragPayload] = await Promise.all([
      getAdminSettings(),
      listAdminAccessKeys({
        q: query,
        status: statusFilter,
        expired: expiredParam,
      }),
      adminApi<Record<string, unknown>>("/v1/admin/rag/status").catch(
        () => null,
      ),
    ])
    setSettings(settingsPayload)
    setKeys(keysPayload.keys)
    setRagStatus(ragPayload)
    setModelDraft({
      baseUrl: settingsPayload.model_base_url ?? "",
      apiKey: "",
      modelName: settingsPayload.model_name ?? "",
      streamingEnabled: settingsPayload.model_streaming_enabled !== false,
    })
  }

  useEffect(() => {
    adminApi<{ authenticated: boolean }>("/v1/admin/me")
      .then((payload) => {
        setAuthenticated(payload.authenticated)
        if (payload.authenticated) void refresh()
      })
      .catch(() => setAuthenticated(false))
    // 初次进入后台只需要执行一次；筛选变更由单独 effect 刷新列表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!authenticated) return
    const timer = window.setTimeout(() => {
      void refreshKeys().catch((err: unknown) =>
        setError(errorMessage(err, "访问 Key 列表刷新失败")),
      )
    }, 250)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authenticated, query, statusFilter, expiryFilter])

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    try {
      await adminApi("/v1/admin/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      })
      setAuthenticated(true)
      await refresh()
    } catch (err) {
      setError(errorMessage(err, "登录失败"))
    }
  }

  const resetCreateForm = () => {
    setKeyLabel("")
    setUsageLimit(10)
    setValidityPreset("30")
    setCustomValidityDays(30)
    setKeyEnabled(true)
    setCreateStep("form")
  }

  const createKey = async () => {
    setError(null)
    setNotice(null)
    try {
      const payload = await createAdminAccessKey({
        label: keyLabel.trim(),
        usage_limit: usageLimit,
        expires_at: createExpiresAt,
        enabled: keyEnabled,
      })
      setRevealedKeyId(payload.record.key_id)
      setRevealedSecret(payload.key)
      setRevealDetail(null)
      resetCreateForm()
      await refreshKeys()
      setNotice(
        "访问 Key 已创建；明文只在当前确认区展示，请立即复制并安全交付。",
      )
    } catch (err) {
      setError(errorMessage(err, "访问 Key 创建失败"))
    }
  }

  const loadKeySessions = async (keyId: string) => {
    setSelectedKey(keyId)
    setSelectedMessages([])
    const payload = await adminApi<{ sessions: KeySession[] }>(
      `/v1/admin/access-keys/${keyId}/sessions`,
    )
    setKeySessions(payload.sessions)
  }

  const loadMessages = async (sessionId: string) => {
    const payload = await adminApi<{ messages: AdminMessage[] }>(
      `/v1/admin/sessions/${sessionId}/messages`,
    )
    setSelectedMessages(payload.messages)
  }

  const revealKey = async (key: AdminAccessKeyRecord) => {
    setError(null)
    setRevealDetail(null)
    setRevealedSecret(null)
    try {
      const payload = await revealAdminAccessKeySecret(key.key_id)
      setRevealedKeyId(key.key_id)
      setRevealedSecret(payload.key)
      setRevealDetail(
        payload.available
          ? null
          : (payload.detail ?? "该访问 Key 的明文不可找回。"),
      )
    } catch (err) {
      setError(errorMessage(err, "访问 Key 明文读取失败"))
    }
  }

  const copySecret = async () => {
    if (!revealedSecret) return
    await navigator.clipboard.writeText(revealedSecret)
    setNotice("已复制选中的访问 Key 明文。")
  }

  const applyToggle = async () => {
    if (!toggleTarget) return
    const { key, nextEnabled } = toggleTarget
    setError(null)
    try {
      await updateAdminAccessKey(key.key_id, { enabled: nextEnabled })
      setToggleTarget(null)
      await refreshKeys()
      if (selectedKey === key.key_id) await loadKeySessions(key.key_id)
      setNotice(
        nextEnabled
          ? "访问 Key 已启用。"
          : "访问 Key 已停用，不能再创建新会话。",
      )
    } catch (err) {
      setError(errorMessage(err, "访问 Key 状态更新失败"))
    }
  }

  const applyQuota = async () => {
    if (!quotaTarget) return
    const { key, mode, value } = quotaTarget
    const nextLimit = mode === "add" ? key.usage_limit + value : value
    setError(null)
    try {
      await updateAdminAccessKey(key.key_id, {
        usage_limit: Math.max(1, nextLimit),
      })
      setQuotaTarget(null)
      await refreshKeys()
      if (selectedKey === key.key_id) await loadKeySessions(key.key_id)
      setNotice("访问 Key 额度已更新。")
    } catch (err) {
      setError(errorMessage(err, "访问 Key 额度更新失败"))
    }
  }

  const updateFeatureFlag = async (patch: Partial<AdminSettings>) => {
    setError(null)
    try {
      const next = await updateAdminSettings(patch)
      setSettings(next)
      setNotice("后台开关已保存。")
    } catch (err) {
      setError(errorMessage(err, "后台开关保存失败"))
    }
  }

  const saveModelSettings = async () => {
    setError(null)
    try {
      const next = await updateAdminSettings({
        model_base_url: modelDraft.baseUrl.trim(),
        model_api_key: modelDraft.apiKey.trim() || undefined,
        model_name: modelDraft.modelName.trim(),
        model_streaming_enabled: modelDraft.streamingEnabled,
      })
      setSettings(next)
      setModelDraft((current) => ({ ...current, apiKey: "" }))
      setNotice("运行时模型配置已保存。")
    } catch (err) {
      setError(errorMessage(err, "运行时模型配置保存失败"))
    }
  }

  const fetchModels = async () => {
    setIsFetchingModels(true)
    setError(null)
    try {
      const payload = await fetchAdminModelConfigModels({
        base_url: modelDraft.baseUrl.trim() || undefined,
        api_key: modelDraft.apiKey.trim() || undefined,
      })
      setAvailableModels(payload.models)
      setModelSource(payload.source ?? null)
      setNotice(`已拉取 ${payload.models.length} 个可用模型。`)
    } catch (err) {
      setError(errorMessage(err, "模型列表拉取失败"))
    } finally {
      setIsFetchingModels(false)
    }
  }

  const saveSelectedModel = async () => {
    await saveModelSettings()
  }

  const testModel = async () => {
    setIsTestingModel(true)
    setModelTestResult(null)
    setError(null)
    try {
      const payload = await testAdminModelConfig({
        base_url: modelDraft.baseUrl.trim() || undefined,
        api_key: modelDraft.apiKey.trim() || undefined,
        model: modelDraft.modelName.trim() || undefined,
      })
      setModelTestResult(payload)
      setNotice(
        payload.ok
          ? "模型连通性测试通过。"
          : "模型连通性测试未通过，请查看返回详情。",
      )
    } catch (err) {
      setError(errorMessage(err, "模型连通性测试失败"))
    } finally {
      setIsTestingModel(false)
    }
  }

  const uploadRagFile = async () => {
    if (!ragFile) return
    try {
      setError(null)
      const formData = new FormData()
      formData.append("file", ragFile)
      const response = await fetch(buildApiUrl("/v1/rag/files"), {
        method: "POST",
        credentials: "include",
        body: formData,
      })
      if (!response.ok) {
        throw new Error(`RAG 上传失败：${response.status}`)
      }
      setRagFile(null)
      const nextStatus = await adminApi<Record<string, unknown>>(
        "/v1/admin/rag/status",
      ).catch(() => null)
      setRagStatus(nextStatus)
      setNotice("知识库文件已上传。")
    } catch (err) {
      setError(errorMessage(err, "RAG 上传失败"))
    }
  }

  if (!authenticated) {
    return (
      <main className="min-h-screen bg-[radial-gradient(circle_at_20%_10%,rgba(37,99,235,.18),transparent_32%),linear-gradient(135deg,#f8fbff,#eaf2ff)] p-6 text-slate-950">
        <form
          onSubmit={handleLogin}
          className="mx-auto mt-24 max-w-md rounded-[28px] border border-white/70 bg-white/70 p-8 shadow-2xl shadow-blue-950/10 backdrop-blur-xl"
        >
          <h1 className="text-2xl font-semibold">模拟面签后台管理</h1>
          <p className="mt-2 text-sm text-slate-500">
            使用管理员密码进入运营控制台。
          </p>
          <input
            className="mt-6 h-12 w-full rounded-2xl border border-slate-200 bg-white/80 px-4"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="管理员密码"
          />
          {error ? (
            <div className="mt-3 text-sm text-red-600">{error}</div>
          ) : null}
          <button
            className="mt-5 h-12 w-full rounded-2xl bg-blue-600 font-semibold text-white shadow-lg shadow-blue-600/20 hover:bg-blue-700"
            type="submit"
          >
            进入后台
          </button>
        </form>
      </main>
    )
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_15%_10%,rgba(37,99,235,.16),transparent_30%),radial-gradient(circle_at_85%_0%,rgba(14,165,233,.14),transparent_28%),linear-gradient(135deg,#f8fbff,#edf4ff)] p-5 text-slate-950">
      <div className="mx-auto max-w-7xl space-y-5">
        <header className="rounded-[28px] border border-white/70 bg-white/75 p-6 shadow-xl shadow-blue-950/10 backdrop-blur-xl">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue-600">
                Operator Console
              </p>
              <h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em]">
                模拟面签后台控制台
              </h1>
              <p className="mt-2 text-sm text-slate-500">
                统一管理客户访问 Key、运行时模型配置、知识库状态与调试能力。
              </p>
            </div>
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
              管理员已登录 · 用户侧模型配置默认由后台集中管控
            </div>
          </div>
          {notice ? (
            <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
              {notice}
            </div>
          ) : null}
          {error ? (
            <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
              {error}
            </div>
          ) : null}
        </header>

        <section className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
          <div className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">客户访问 Key 管理</h2>
                <p className="mt-1 text-sm text-slate-500">
                  列表默认只展示元数据；明文需要对单个 Key 显式读取。
                </p>
              </div>
              <button
                className="rounded-2xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-blue-600/20 hover:bg-blue-700"
                onClick={() => {
                  setCreateDialogOpen(true)
                  setRevealDetail(null)
                }}
              >
                创建访问 Key
              </button>
            </div>

            <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_150px_150px_auto]">
              <input
                className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索备注、Key ID 或 masked preview"
              />
              <select
                className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
                value={statusFilter}
                onChange={(event) => {
                  const value = event.target.value
                  if (isAdminAccessKeyStatusFilter(value)) {
                    setStatusFilter(value)
                  }
                }}
              >
                <option value="all">全部状态</option>
                <option value="enabled">启用</option>
                <option value="disabled">停用</option>
              </select>
              <select
                className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
                value={expiryFilter}
                onChange={(event) => {
                  const value = event.target.value
                  if (isExpiryFilter(value)) {
                    setExpiryFilter(value)
                  }
                }}
              >
                <option value="all">全部有效期</option>
                <option value="active">未过期</option>
                <option value="expired">已过期</option>
              </select>
              <button
                className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4 text-sm font-semibold text-slate-700"
                onClick={() => void refreshKeys()}
              >
                刷新
              </button>
            </div>

            <div className="mt-4 space-y-3">
              {keys.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-white/55 p-6 text-sm text-slate-500">
                  暂无匹配的访问 Key。
                </div>
              ) : null}
              {keys.map((item) => (
                <article
                  key={item.key_id}
                  className="rounded-2xl border border-slate-200 bg-white/70 p-4 shadow-sm transition hover:border-blue-200 hover:bg-blue-50/50"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <button
                      onClick={() => void loadKeySessions(item.key_id)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold">
                          {item.label || `客户 ${item.key_id}`}
                        </span>
                        <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-600">
                          {keyStatusLabel(item)}
                        </span>
                        <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-slate-600">
                          {normalizeKeyPreview(item)}
                        </span>
                      </div>
                      <div className="mt-2 grid gap-1 text-xs text-slate-500 sm:grid-cols-2 lg:grid-cols-4">
                        <span>
                          使用：{item.usage_count}/{item.usage_limit}
                        </span>
                        <span>剩余额度：{item.remaining_uses}</span>
                        <span>有效期：{formatDateTime(item.expires_at)}</span>
                        <span>
                          最近使用：{formatDateTime(item.last_used_at, "无")}
                        </span>
                      </div>
                    </button>
                    <div className="text-right text-xs text-slate-500">
                      <div>
                        ID：<span className="font-mono">{item.key_id}</span>
                      </div>
                      <div>
                        Secret：
                        {item.secret_available === false
                          ? "不可找回"
                          : "可读取"}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <button
                      className="rounded-xl border border-blue-200 bg-blue-50 px-3 py-1.5 font-medium text-blue-700 disabled:opacity-50"
                      disabled={item.secret_available === false}
                      onClick={() => void revealKey(item)}
                    >
                      读取/复制该 Key
                    </button>
                    <button
                      className="rounded-xl border border-slate-200 bg-white/80 px-3 py-1.5 font-medium text-slate-700"
                      onClick={() =>
                        setToggleTarget({
                          key: item,
                          nextEnabled: !item.enabled,
                        })
                      }
                    >
                      {item.enabled ? "停用" : "启用"}
                    </button>
                    <button
                      className="rounded-xl border border-slate-200 bg-white/80 px-3 py-1.5 font-medium text-slate-700"
                      onClick={() =>
                        setQuotaTarget({ key: item, mode: "add", value: 1 })
                      }
                    >
                      调整额度
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>

          <div className="space-y-5">
            <div className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
              <h2 className="text-lg font-semibold">选中 Key 明文</h2>
              <p className="mt-1 text-sm text-slate-500">
                仅对最近读取或创建的单个 Key 展示，避免批量泄露。
              </p>
              <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="text-xs text-slate-500">
                  当前选择：{revealedKeyId ?? "未选择"}
                </div>
                {revealedSecret ? (
                  <code className="mt-2 block break-all rounded-xl bg-white p-3 text-sm text-slate-800">
                    {revealedSecret}
                  </code>
                ) : (
                  <div className="mt-2 text-sm text-slate-500">
                    {revealDetail ?? "请选择一个可读取的访问 Key。"}
                  </div>
                )}
                <button
                  className="mt-3 rounded-xl bg-slate-950 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40"
                  disabled={!revealedSecret}
                  onClick={() => void copySecret()}
                >
                  复制当前 Key
                </button>
              </div>
            </div>

            <div className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
              <h2 className="text-lg font-semibold">功能开关</h2>
              <div className="mt-4 grid gap-3 text-sm">
                {(
                  [
                    ["show_github_link", "显示 GitHub 信息"],
                    ["debug_console_enabled", "开放调试台"],
                    ["debug_material_enabled", "开放调试材料"],
                    ["rag_status_user_visible", "用户侧显示知识库状态"],
                  ] as const
                ).map(([key, label]) => (
                  <label
                    key={key}
                    className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white/60 px-4 py-3"
                  >
                    <span>{label}</span>
                    <input
                      type="checkbox"
                      checked={Boolean(settings?.[key])}
                      onChange={(event) =>
                        void updateFeatureFlag({ [key]: event.target.checked })
                      }
                    />
                  </label>
                ))}
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                  用户侧模型参数由后台集中配置；当前页面不再向用户开放 Base URL
                  / API Key 输入入口。
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">运行时模型配置</h2>
              <p className="mt-1 text-sm text-slate-500">
                后台保存的配置会作为模拟面签运行时模型来源；API Key
                留空时保持既有密钥不变。
              </p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white/70 px-4 py-2 text-xs text-slate-600">
              Key 状态：
              {settings?.model_api_key_configured ? "已配置" : "未配置"}
            </div>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-[1.2fr_1fr_1fr_auto]">
            <input
              value={modelDraft.baseUrl}
              onChange={(event) =>
                setModelDraft((current) => ({
                  ...current,
                  baseUrl: event.target.value,
                }))
              }
              className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
              placeholder="Base URL，例如 https://.../v1"
            />
            <input
              value={modelDraft.apiKey}
              onChange={(event) =>
                setModelDraft((current) => ({
                  ...current,
                  apiKey: event.target.value,
                }))
              }
              type="password"
              className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
              placeholder={
                settings?.model_api_key_configured
                  ? "已配置；留空不修改"
                  : "API Key"
              }
            />
            {availableModels.length ? (
              <select
                value={modelDraft.modelName}
                onChange={(event) =>
                  setModelDraft((current) => ({
                    ...current,
                    modelName: event.target.value,
                  }))
                }
                className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
              >
                <option value="">选择模型</option>
                {availableModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.label || model.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={modelDraft.modelName}
                onChange={(event) =>
                  setModelDraft((current) => ({
                    ...current,
                    modelName: event.target.value,
                  }))
                }
                className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4"
                placeholder="模型名称"
              />
            )}
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/60 px-4 text-sm">
              <input
                type="checkbox"
                checked={modelDraft.streamingEnabled}
                onChange={(event) =>
                  setModelDraft((current) => ({
                    ...current,
                    streamingEnabled: event.target.checked,
                  }))
                }
              />
              流式输出
            </label>
          </div>
          <div className="mt-4 flex flex-wrap gap-2 text-sm">
            <button
              className="rounded-2xl bg-blue-600 px-4 py-2 font-semibold text-white"
              onClick={() => void saveModelSettings()}
            >
              Save
            </button>
            <button
              className="rounded-2xl border border-slate-200 bg-white px-4 py-2 font-semibold text-slate-700 disabled:opacity-50"
              onClick={() => void fetchModels()}
              disabled={isFetchingModels}
            >
              {isFetchingModels ? "Fetching..." : "Fetch Models"}
            </button>
            <button
              className="rounded-2xl border border-slate-200 bg-white px-4 py-2 font-semibold text-slate-700"
              onClick={() => void saveSelectedModel()}
            >
              Save Model
            </button>
            <button
              className="rounded-2xl bg-slate-950 px-4 py-2 font-semibold text-white disabled:opacity-50"
              onClick={() => void testModel()}
              disabled={isTestingModel}
            >
              {isTestingModel ? "Testing..." : "Test"}
            </button>
            {modelSource ? (
              <span className="self-center text-xs text-slate-500">
                模型来源：{modelSource}
              </span>
            ) : null}
          </div>
          {modelTestResult ? (
            <div
              className={`mt-4 rounded-2xl border px-4 py-3 text-sm ${modelTestResult.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}
            >
              <div className="font-semibold">
                测试结果：{modelTestResult.ok ? "通过" : "未通过"}
              </div>
              <div className="mt-1 text-xs leading-5">
                模型：
                {(modelTestResult.model ?? modelDraft.modelName) || "未返回"} ·
                延迟：{modelTestResult.latency_ms ?? "-"}ms · 来源：
                {modelTestResult.source ?? "-"}
                {modelTestResult.upstream?.error_category
                  ? ` · 错误类别：${modelTestResult.upstream.error_category}`
                  : ""}
                {modelTestResult.upstream?.upstream_code
                  ? ` · 上游码：${modelTestResult.upstream.upstream_code}`
                  : ""}
              </div>
              {modelTestResult.detail ? (
                <div className="mt-1 text-xs leading-5">
                  详情：{modelTestResult.detail}
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="grid gap-5 lg:grid-cols-[0.95fr_1.05fr]">
          <div className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <h2 className="text-lg font-semibold">Key 会话</h2>
            <p className="mt-1 text-sm text-slate-500">
              {selectedKeyRecord
                ? `当前 Key：${selectedKeyRecord.label || selectedKeyRecord.key_id}`
                : "选择访问 Key 查看关联会话。"}
            </p>
            <div className="mt-4 space-y-2">
              {keySessions.map((item) => (
                <button
                  key={item.session_id}
                  onClick={() => void loadMessages(item.session_id)}
                  className="w-full rounded-2xl border border-slate-200 bg-white/65 px-4 py-3 text-left hover:bg-blue-50"
                >
                  <div className="font-medium">{item.session_id}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {item.declared_family ?? "unknown"} · {item.message_count}{" "}
                    条消息 · {item.phase_state ?? "phase unknown"}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <h2 className="text-lg font-semibold">会话消息</h2>
            <div className="mt-4 max-h-[520px] space-y-2 overflow-auto rounded-2xl border border-slate-200 bg-white/50 p-3">
              {selectedMessages.length === 0 ? (
                <div className="p-4 text-sm text-slate-500">
                  选择一个会话后显示消息。
                </div>
              ) : null}
              {selectedMessages.map((message) => (
                <div
                  key={message.turn_id}
                  className="rounded-xl bg-white/80 px-3 py-2 text-sm"
                >
                  <div className="text-xs font-medium text-blue-700">
                    {message.role} · #{message.turn_index}
                  </div>
                  <div className="mt-1 whitespace-pre-wrap text-slate-700">
                    {message.content}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="rounded-[24px] border border-white/70 bg-white/75 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
          <h2 className="text-lg font-semibold">RAG / 知识库状态</h2>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <input
              type="file"
              onChange={(event) => setRagFile(event.target.files?.[0] ?? null)}
              className="rounded-2xl border border-slate-200 bg-white/70 px-3 py-2 text-sm"
            />
            <button
              className="rounded-2xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              onClick={() => void uploadRagFile()}
              disabled={!ragFile}
            >
              上传到知识库
            </button>
          </div>
          <pre className="mt-4 max-h-80 overflow-auto rounded-2xl bg-slate-950 p-4 text-xs text-slate-100">
            {JSON.stringify(ragStatus, null, 2)}
          </pre>
        </section>
      </div>

      {createDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm">
          <div className="w-full max-w-2xl rounded-[28px] border border-white/70 bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold">创建客户访问 Key</h2>
                <p className="mt-1 text-sm text-slate-500">
                  默认有效期 30 天；创建前需要二次确认。
                </p>
              </div>
              <button
                className="rounded-xl border border-slate-200 px-3 py-1 text-sm"
                onClick={() => {
                  setCreateDialogOpen(false)
                  resetCreateForm()
                }}
              >
                关闭
              </button>
            </div>
            {createStep === "form" ? (
              <div className="mt-5 grid gap-4">
                <label className="grid gap-2 text-sm font-medium">
                  客户备注
                  <input
                    className="h-11 rounded-2xl border border-slate-200 px-4 font-normal"
                    value={keyLabel}
                    onChange={(event) => setKeyLabel(event.target.value)}
                    placeholder="例如：客户 A / 6 月批次"
                  />
                </label>
                <label className="grid gap-2 text-sm font-medium">
                  会话额度
                  <input
                    className="h-11 rounded-2xl border border-slate-200 px-4 font-normal"
                    type="number"
                    min={1}
                    max={1000}
                    value={usageLimit}
                    onChange={(event) =>
                      setUsageLimit(Number(event.target.value) || 1)
                    }
                  />
                </label>
                <div className="grid gap-2 text-sm font-medium">
                  有效期
                  <div className="grid gap-2 sm:grid-cols-5">
                    {VALIDITY_PRESETS.map((preset) => (
                      <button
                        key={preset}
                        className={`rounded-2xl border px-3 py-2 text-sm ${validityPreset === preset ? "border-blue-500 bg-blue-50 text-blue-700" : "border-slate-200 bg-white text-slate-700"}`}
                        onClick={() => setValidityPreset(preset)}
                      >
                        {preset === "custom"
                          ? "自定义"
                          : preset === "never"
                            ? "永不过期"
                            : `${preset} 天`}
                      </button>
                    ))}
                  </div>
                  {validityPreset === "custom" ? (
                    <input
                      className="h-11 rounded-2xl border border-slate-200 px-4 font-normal"
                      type="number"
                      min={1}
                      max={3650}
                      value={customValidityDays}
                      onChange={(event) =>
                        setCustomValidityDays(Number(event.target.value) || 1)
                      }
                      placeholder="自定义天数"
                    />
                  ) : null}
                </div>
                <label className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3 text-sm font-medium">
                  创建后立即启用
                  <input
                    type="checkbox"
                    checked={keyEnabled}
                    onChange={(event) => setKeyEnabled(event.target.checked)}
                  />
                </label>
                <div className="flex justify-end gap-2">
                  <button
                    className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold"
                    onClick={() => {
                      setCreateDialogOpen(false)
                      resetCreateForm()
                    }}
                  >
                    取消
                  </button>
                  <button
                    className="rounded-2xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white"
                    onClick={() => setCreateStep("confirm")}
                  >
                    继续确认
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-5 space-y-4">
                <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
                  即将创建访问 Key：备注「{keyLabel || "未填写"}」，额度{" "}
                  {usageLimit} 次，有效期{" "}
                  {createExpiresAt
                    ? formatDateTime(createExpiresAt)
                    : "长期有效"}
                  ，状态 {keyEnabled ? "启用" : "停用"}。
                  创建后明文只在当前页面展示，请确认后复制保存。
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold"
                    onClick={() => setCreateStep("form")}
                  >
                    返回修改
                  </button>
                  <button
                    className="rounded-2xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white"
                    onClick={() => {
                      void createKey()
                      setCreateDialogOpen(false)
                    }}
                  >
                    确认创建
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}

      {toggleTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-[28px] bg-white p-6 shadow-2xl">
            <h2 className="text-xl font-semibold">
              确认{toggleTarget.nextEnabled ? "启用" : "停用"}访问 Key
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              目标：{toggleTarget.key.label || toggleTarget.key.key_id}。
              {toggleTarget.nextEnabled
                ? "启用后可继续创建新会话。"
                : "停用后不会删除历史会话，但不能再创建新会话。"}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold"
                onClick={() => setToggleTarget(null)}
              >
                取消
              </button>
              <button
                className="rounded-2xl bg-slate-950 px-4 py-2 text-sm font-semibold text-white"
                onClick={() => void applyToggle()}
              >
                确认
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {quotaTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-[28px] bg-white p-6 shadow-2xl">
            <h2 className="text-xl font-semibold">调整访问额度</h2>
            <p className="mt-2 text-sm text-slate-500">
              当前额度 {quotaTarget.key.usage_limit}，已使用{" "}
              {quotaTarget.key.usage_count}。
            </p>
            <div className="mt-4 grid gap-3">
              <select
                className="h-11 rounded-2xl border border-slate-200 px-4"
                value={quotaTarget.mode}
                onChange={(event) => {
                  const value = event.target.value
                  if (isQuotaMode(value)) {
                    setQuotaTarget({
                      ...quotaTarget,
                      mode: value,
                    })
                  }
                }}
              >
                <option value="set">精确设置总额度</option>
                <option value="add">在当前额度上追加</option>
              </select>
              <input
                className="h-11 rounded-2xl border border-slate-200 px-4"
                type="number"
                min={1}
                max={1000}
                value={quotaTarget.value}
                onChange={(event) =>
                  setQuotaTarget({
                    ...quotaTarget,
                    value: Number(event.target.value) || 1,
                  })
                }
              />
              <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                确认后总额度将变为{" "}
                {quotaTarget.mode === "add"
                  ? quotaTarget.key.usage_limit + quotaTarget.value
                  : quotaTarget.value}
                。
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold"
                onClick={() => setQuotaTarget(null)}
              >
                取消
              </button>
              <button
                className="rounded-2xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white"
                onClick={() => void applyQuota()}
              >
                保存额度
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  )
}
