"use client"

import { FormEvent, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { ArrowRight, BarChart3, KeyRound, LinkIcon, LockKeyhole, Radar, Sparkles } from "lucide-react"
import { buildApiUrl } from "@/lib/api/config"
import {
  clearAdminAccessKeyMaterials,
  createAdminAccessKey,
  fetchAdminModelConfigModels,
  getAdminLoginAudit,
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
  AdminLoginAuditEvent,
  AdminLoginAuditIpStat,
  AdminModelConfigTestResponse,
  AdminSettings,
  ModelListItem,
} from "@/lib/api/types"
import { buildAccessKeyShareLink } from "@/lib/access-key-share"

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

const ADMIN_NAV_ITEMS = [
  { label: "总览", href: "#overview" },
  { label: "访问 Key", href: "#access-keys" },
  { label: "登录审计", href: "#login-audit" },
  { label: "模型配置", href: "#model-config" },
  { label: "知识库", href: "#knowledge" },
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
type MaterialCleanupTarget = AdminAccessKeyRecord | null

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

async function copyTextToClipboard(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch {
    return false
  }
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
  const [auditEvents, setAuditEvents] = useState<AdminLoginAuditEvent[]>([])
  const [ipStats, setIpStats] = useState<AdminLoginAuditIpStat[]>([])
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
  const [revealedShareLink, setRevealedShareLink] = useState<string | null>(null)
  const [toggleTarget, setToggleTarget] = useState<ToggleTarget>(null)
  const [quotaTarget, setQuotaTarget] = useState<QuotaTarget>(null)
  const [materialCleanupTarget, setMaterialCleanupTarget] =
    useState<MaterialCleanupTarget>(null)
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

  const auditSummary = useMemo(() => {
    const successCount = ipStats.reduce((total, item) => total + item.success_count, 0)
    const failureCount = ipStats.reduce((total, item) => total + item.failure_count, 0)
    return {
      uniqueIpCount: ipStats.length,
      successCount,
      failureCount,
      totalCount: successCount + failureCount,
      latestIp: auditEvents[0]?.client_ip ?? "-",
    }
  }, [auditEvents, ipStats])

  const refreshKeys = async () => {
    const payload = await listAdminAccessKeys({
      q: query,
      status: statusFilter,
      expired: expiredParam,
    })
    setKeys(payload.keys)
  }

  const refreshAudit = async () => {
    const payload = await getAdminLoginAudit({ limit: 120 })
    setAuditEvents(payload.events)
    setIpStats(payload.ip_stats)
  }

  const refresh = async () => {
    const [settingsPayload, keysPayload, auditPayload, ragPayload] = await Promise.all([
      getAdminSettings(),
      listAdminAccessKeys({
        q: query,
        status: statusFilter,
        expired: expiredParam,
      }),
      getAdminLoginAudit({ limit: 120 }),
      adminApi<Record<string, unknown>>("/v1/admin/rag/status").catch(
        () => null,
      ),
    ])
    setSettings(settingsPayload)
    setKeys(keysPayload.keys)
    setAuditEvents(auditPayload.events)
    setIpStats(auditPayload.ip_stats)
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

  const rememberRevealedSecret = (keyId: string, secret: string | null) => {
    setRevealedKeyId(keyId)
    setRevealedSecret(secret)
    setRevealedShareLink(secret ? buildAccessKeyShareLink(secret) : null)
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
      rememberRevealedSecret(payload.record.key_id, payload.key)
      setRevealDetail(null)
      resetCreateForm()
      await Promise.all([refreshKeys(), refreshAudit()])
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
    rememberRevealedSecret(key.key_id, null)
    try {
      const payload = await revealAdminAccessKeySecret(key.key_id)
      rememberRevealedSecret(key.key_id, payload.key)
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
    const ok = await copyTextToClipboard(revealedSecret)
    setNotice(ok ? "已复制访问 Key。" : "复制失败，请手动复制当前 Key。")
  }

  const copySecretForKey = async (key: AdminAccessKeyRecord) => {
    setError(null)
    setNotice(null)
    try {
      const payload = await revealAdminAccessKeySecret(key.key_id)
      rememberRevealedSecret(key.key_id, payload.key)
      setRevealDetail(
        payload.available
          ? null
          : (payload.detail ?? "该访问 Key 的明文不可找回。"),
      )
      if (!payload.key) {
        setNotice(payload.detail ?? "该访问 Key 的明文不可找回。")
        return
      }
      const ok = await copyTextToClipboard(payload.key)
      setNotice(ok ? "已复制访问 Key。" : "读取成功，但复制失败；请在右侧确认区手动复制。")
    } catch (err) {
      setError(errorMessage(err, "访问 Key 复制失败"))
    }
  }

  const copyShareLink = async () => {
    if (!revealedShareLink) return
    const ok = await copyTextToClipboard(revealedShareLink)
    setNotice(ok ? "已复制一键分享链接。" : "复制失败，请手动复制当前分享链接。")
  }

  const copyShareLinkForKey = async (key: AdminAccessKeyRecord) => {
    setError(null)
    setNotice(null)
    try {
      const payload = await revealAdminAccessKeySecret(key.key_id)
      rememberRevealedSecret(key.key_id, payload.key)
      setRevealDetail(
        payload.available
          ? null
          : (payload.detail ?? "该访问 Key 的明文不可找回。"),
      )
      if (!payload.key) {
        setNotice(payload.detail ?? "该访问 Key 的明文不可找回。")
        return
      }
      const link = buildAccessKeyShareLink(payload.key)
      setRevealedShareLink(link)
      const ok = await copyTextToClipboard(link)
      setNotice(ok ? "已复制一键分享链接。" : "读取成功，但复制失败；请在右侧确认区手动复制分享链接。")
    } catch (err) {
      setError(errorMessage(err, "分享链接生成失败"))
    }
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

  const applyMaterialCleanup = async () => {
    if (!materialCleanupTarget) return
    setError(null)
    try {
      const result = await clearAdminAccessKeyMaterials(
        materialCleanupTarget.key_id,
      )
      setMaterialCleanupTarget(null)
      await refreshKeys()
      if (selectedKey === result.key_id) {
        await loadKeySessions(result.key_id)
        setSelectedMessages([])
      }
      setNotice(
        `已清理 ${result.cleared_document_count} 份资料，保留 ${result.skipped_template_count} 份模板/归档资料；会话、消息和访问 Key 未删除。`,
      )
    } catch (err) {
      setError(errorMessage(err, "访问 Key 资料清理失败"))
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
      <main className="min-h-[100dvh] overflow-hidden bg-[#050608] p-5 text-white">
        <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_18%_10%,rgba(59,130,246,0.22),transparent_32%),radial-gradient(circle_at_82%_16%,rgba(168,85,247,0.16),transparent_28%),linear-gradient(180deg,rgba(255,255,255,0.045),transparent_32%)]" />
        <div className="relative mx-auto flex min-h-[calc(100dvh-40px)] max-w-7xl items-center justify-center">
          <form
            onSubmit={handleLogin}
            className="w-full max-w-md overflow-hidden rounded-[2rem] border border-white/12 bg-white/[0.045] p-7 shadow-2xl shadow-cyan-950/30 backdrop-blur-2xl"
          >
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-200/15 bg-cyan-200/[0.06] px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.2em] text-cyan-100/80">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-200 shadow-[0_0_16px_rgba(125,211,252,0.9)]" />
              Operator console
            </div>
            <h1 className="mt-6 text-4xl font-black tracking-[-0.04em]">后台管理</h1>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              使用管理员密码进入运营控制台，管理访问 Key、登录审计、模型配置与知识库状态。
            </p>
            <div className="relative mt-6">
              <LockKeyhole className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-white/34" />
              <input
                className="h-12 w-full rounded-2xl border border-white/10 bg-white/[0.055] pl-11 pr-4 text-white shadow-inner shadow-white/[0.03] placeholder:text-slate-400 focus:border-cyan-200/40 focus:outline-none"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="管理员密码"
              />
            </div>
            {error ? (
              <div className="mt-3 rounded-2xl border border-red-300/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">{error}</div>
            ) : null}
            <button
              className="group mt-5 flex h-12 w-full items-center justify-between rounded-2xl bg-white px-4 text-base font-black text-black shadow-2xl shadow-cyan-200/10 transition hover:-translate-y-0.5 hover:bg-cyan-50"
              type="submit"
            >
              <span className="inline-flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                进入后台
              </span>
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-black text-white transition group-hover:translate-x-0.5">
                <ArrowRight className="h-4 w-4" />
              </span>
            </button>
          </form>
        </div>
      </main>
    )
  }

  return (
    <main className="min-h-[100dvh] overflow-hidden bg-[#050608] p-5 text-white">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_18%_10%,rgba(59,130,246,0.20),transparent_32%),radial-gradient(circle_at_82%_16%,rgba(168,85,247,0.16),transparent_28%),linear-gradient(180deg,rgba(255,255,255,0.045),transparent_32%)]" />
      <div className="relative mx-auto max-w-7xl space-y-5">
        <header className="sticky top-4 z-20 rounded-[2rem] border border-white/10 bg-black/35 px-5 py-4 shadow-2xl shadow-black/20 backdrop-blur-2xl">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <Link href="/" className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-full border border-white/12 bg-white/[0.06] shadow-inner shadow-white/10">
                <Sparkles className="h-4 w-4 text-cyan-200" />
              </span>
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-cyan-200">Operator Console</p>
                <h1 className="text-lg font-black tracking-[-0.03em]">DS-160 后台控制台</h1>
              </div>
            </Link>
            <nav className="hidden items-center gap-5 text-sm text-white/58 lg:flex">
              {ADMIN_NAV_ITEMS.map((item) => (
                <a key={item.href} href={item.href} className="transition hover:text-white">
                  {item.label}
                </a>
              ))}
            </nav>
            <div className="rounded-full border border-emerald-300/20 bg-emerald-300/10 px-4 py-2 text-sm text-emerald-100">
              管理员已登录
            </div>
          </div>
          {notice ? (
            <div className="mt-4 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-4 py-2 text-sm text-emerald-100">
              {notice}
            </div>
          ) : null}
          {error ? (
            <div className="mt-4 rounded-2xl border border-red-300/20 bg-red-500/10 px-4 py-2 text-sm text-red-100">
              {error}
            </div>
          ) : null}
        </header>

        <section id="overview" className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "访问 Key", value: String(keys.length), detail: "当前筛选结果", icon: KeyRound },
            { label: "登录 IP", value: String(auditSummary.uniqueIpCount), detail: `最近：${auditSummary.latestIp}`, icon: Radar },
            { label: "成功登录", value: String(auditSummary.successCount), detail: "审计窗口内", icon: BarChart3 },
            { label: "失败登录", value: String(auditSummary.failureCount), detail: "可用于排查异常", icon: LockKeyhole },
          ].map((card) => (
            <article key={card.label} className="rounded-[1.5rem] border border-white/10 bg-white/[0.045] p-5 shadow-2xl shadow-black/20 backdrop-blur-2xl">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/60">{card.label}</p>
                  <div className="mt-3 text-3xl font-black tracking-[-0.04em]">{card.value}</div>
                  <p className="mt-1 text-sm text-slate-400">{card.detail}</p>
                </div>
                <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.055] text-cyan-100">
                  <card.icon className="h-4 w-4" />
                </span>
              </div>
            </article>
          ))}
        </section>

        <section id="access-keys" className="grid gap-5 xl:grid-cols-[1.25fr_0.75fr]">
          <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">客户访问 Key 管理</h2>
                <p className="mt-1 text-sm text-slate-400">
                  列表默认只展示元数据；明文需要对单个 Key 显式读取。
                </p>
              </div>
              <button
                className="rounded-2xl bg-white px-4 py-2 text-sm font-semibold text-black shadow-lg shadow-white/10 hover:bg-cyan-50"
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
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索备注、Key ID 或 masked preview"
              />
              <select
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
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
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
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
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-sm font-semibold text-slate-200"
                onClick={() => void refreshKeys()}
              >
                刷新
              </button>
            </div>

            <div className="mt-4 space-y-3">
              {keys.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.045] p-6 text-sm text-slate-400">
                  暂无匹配的访问 Key。
                </div>
              ) : null}
              {keys.map((item) => (
                <article
                  key={item.key_id}
                  className="rounded-2xl border border-white/10 bg-white/[0.055] p-4 shadow-sm transition hover:border-cyan-200/20 hover:bg-cyan-200/10"
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
                        <span className="rounded-full border border-white/10 bg-white/[0.08] px-2 py-0.5 text-[11px] text-slate-300">
                          {keyStatusLabel(item)}
                        </span>
                        <span className="rounded-full border border-white/10 bg-white/[0.045] px-2 py-0.5 font-mono text-[11px] text-slate-300">
                          {normalizeKeyPreview(item)}
                        </span>
                      </div>
                      <div className="mt-2 grid gap-1 text-xs text-slate-400 sm:grid-cols-2 lg:grid-cols-4">
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
                    <div className="text-right text-xs text-slate-400">
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
                      className="rounded-xl border border-cyan-200/20 bg-cyan-200/10 px-3 py-1.5 font-medium text-cyan-200 disabled:opacity-50"
                      disabled={item.secret_available === false}
                      onClick={() => void revealKey(item)}
                    >
                      显示明文
                    </button>
                    <button
                      className="rounded-xl border border-cyan-200/20 bg-cyan-200/10 px-3 py-1.5 font-medium text-cyan-200 disabled:opacity-50"
                      disabled={item.secret_available === false}
                      onClick={() => void copySecretForKey(item)}
                    >
                      复制 Key
                    </button>
                    <button
                      className="rounded-xl border border-violet-200/20 bg-violet-200/10 px-3 py-1.5 font-medium text-violet-100 disabled:opacity-50"
                      disabled={item.secret_available === false}
                      onClick={() => void copyShareLinkForKey(item)}
                    >
                      一键分享链接
                    </button>
                    <button
                      className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-1.5 font-medium text-slate-200"
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
                      className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-1.5 font-medium text-slate-200"
                      onClick={() =>
                        setQuotaTarget({ key: item, mode: "add", value: 1 })
                      }
                    >
                      调整额度
                    </button>
                    <button
                      className="rounded-xl border border-red-300/20 bg-red-500/10 px-3 py-1.5 font-medium text-red-100"
                      onClick={() => setMaterialCleanupTarget(item)}
                    >
                      清理该 Key 资料
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>

          <div className="space-y-5">
            <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
              <h2 className="text-lg font-semibold">选中 Key 明文</h2>
              <p className="mt-1 text-sm text-slate-400">
                仅对最近读取或创建的单个 Key 展示，避免批量泄露。
              </p>
              <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.045] p-4">
                <div className="text-xs text-slate-400">
                  当前选择：{revealedKeyId ?? "未选择"}
                </div>
                {revealedSecret ? (
                  <>
                    <code className="mt-2 block break-all rounded-xl bg-white/[0.08] p-3 text-sm text-slate-100">
                      {revealedSecret}
                    </code>
                    {revealedShareLink ? (
                      <div className="mt-3 rounded-xl border border-violet-200/15 bg-violet-200/[0.06] p-3">
                        <div className="flex items-center gap-2 text-xs font-semibold text-violet-100">
                          <LinkIcon className="h-3.5 w-3.5" />
                          一键分享链接
                        </div>
                        <code className="mt-2 block break-all text-xs leading-5 text-slate-200">
                          {revealedShareLink}
                        </code>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="mt-2 text-sm text-slate-400">
                    {revealDetail ?? "请选择一个可读取的访问 Key。"}
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    className="rounded-xl bg-cyan-200 px-3 py-2 text-xs font-semibold text-slate-950 shadow-lg shadow-cyan-950/20 transition hover:bg-cyan-100 disabled:bg-white/[0.08] disabled:text-slate-500 disabled:shadow-none"
                    disabled={!revealedSecret}
                    onClick={() => void copySecret()}
                  >
                    复制当前 Key
                  </button>
                  <button
                    className="rounded-xl bg-violet-200 px-3 py-2 text-xs font-semibold text-slate-950 shadow-lg shadow-violet-950/20 transition hover:bg-violet-100 disabled:bg-white/[0.08] disabled:text-slate-500 disabled:shadow-none"
                    disabled={!revealedShareLink}
                    onClick={() => void copyShareLink()}
                  >
                    复制分享链接
                  </button>
                </div>
              </div>
            </div>

            <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
              <h2 className="text-lg font-semibold">功能开关</h2>
              <div className="mt-4 grid gap-3 text-sm">
                {(
                  [
                    ["show_github_link", "显示 GitHub 信息"],
                    ["wx_entry_enabled", "微信端入口 / 微信 web-view MVP"],
                    ["debug_console_enabled", "开放调试台"],
                    ["debug_material_enabled", "开放调试材料"],
                    ["rag_status_user_visible", "用户侧显示知识库状态"],
                  ] as const
                ).map(([key, label]) => (
                  <label
                    key={key}
                    className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-3"
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
                <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-xs leading-5 text-amber-100">
                  用户侧模型参数由后台集中配置；当前页面不再向用户开放 Base URL
                  / API Key 输入入口。
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="login-audit" className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">登录审计</h2>
                <p className="mt-1 text-sm text-slate-400">完整显示登录 IP，不打码；优先识别 Cloudflare 的 CF-Connecting-IP。</p>
              </div>
              <button
                className="rounded-2xl border border-white/10 bg-white/[0.08] px-4 py-2 text-sm font-semibold text-slate-200"
                onClick={() => void refreshAudit()}
              >
                刷新审计
              </button>
            </div>
            <div className="mt-4 max-h-[520px] space-y-2 overflow-auto">
              {auditEvents.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.045] p-6 text-sm text-slate-400">暂无登录审计记录。</div>
              ) : null}
              {auditEvents.map((event) => (
                <article key={event.id} className="rounded-2xl border border-white/10 bg-white/[0.055] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-mono text-base font-semibold text-white">{event.client_ip}</div>
                      <div className="mt-1 text-xs text-slate-400">
                        {formatDateTime(event.occurred_at, "-")} · {event.session_kind} · {event.client_ip_source}
                      </div>
                    </div>
                    <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${event.outcome === "success" ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100" : "border-red-300/20 bg-red-500/10 text-red-100"}`}>
                      {event.outcome === "success" ? "成功" : "失败"}
                    </span>
                  </div>
                  <div className="mt-3 grid gap-1 text-xs text-slate-400 sm:grid-cols-2">
                    <span>Access Key：{event.access_key_id ?? "-"}</span>
                    <span>失败原因：{event.failure_reason ?? "-"}</span>
                    <span>CF 国家：{event.cf_country ?? "-"}</span>
                    <span>CF Ray：{event.cf_ray ?? "-"}</span>
                  </div>
                </article>
              ))}
            </div>
          </div>
          <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
            <h2 className="text-lg font-semibold">IP 登录次数</h2>
            <p className="mt-1 text-sm text-slate-400">按完整 IP 聚合成功 / 失败次数。</p>
            <div className="mt-4 space-y-2">
              {ipStats.map((item) => (
                <div key={item.client_ip} className="rounded-2xl border border-white/10 bg-white/[0.055] px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-sm font-semibold">{item.client_ip}</span>
                    <span className="rounded-full bg-white/[0.08] px-2 py-1 text-xs text-slate-300">总计 {item.total_count}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-400">
                    <span>成功 {item.success_count}</span>
                    <span>失败 {item.failure_count}</span>
                    <span>{formatDateTime(item.last_seen_at, "-")}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="model-config" className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">运行时模型配置</h2>
              <p className="mt-1 text-sm text-slate-400">
                后台保存的配置会作为模拟面签运行时模型来源；API Key
                留空时保持既有密钥不变。
              </p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.055] px-4 py-2 text-xs text-slate-300">
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
              className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
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
              className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
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
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
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
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white placeholder:text-slate-500"
                placeholder="模型名称"
              />
            )}
            <label className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.05] px-4 text-sm">
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
              className="rounded-2xl bg-white px-4 py-2 font-semibold text-slate-950 shadow-lg shadow-white/10 transition hover:bg-cyan-50"
              onClick={() => void saveModelSettings()}
            >
              Save
            </button>
            <button
              className="rounded-2xl border border-white/10 bg-white/[0.08] px-4 py-2 font-semibold text-slate-200 disabled:opacity-50"
              onClick={() => void fetchModels()}
              disabled={isFetchingModels}
            >
              {isFetchingModels ? "Fetching..." : "Fetch Models"}
            </button>
            <button
              className="rounded-2xl border border-white/10 bg-white/[0.08] px-4 py-2 font-semibold text-slate-200"
              onClick={() => void saveSelectedModel()}
            >
              Save Model
            </button>
            <button
              className="rounded-2xl bg-black/60 px-4 py-2 font-semibold text-white disabled:opacity-50"
              onClick={() => void testModel()}
              disabled={isTestingModel}
            >
              {isTestingModel ? "Testing..." : "Test"}
            </button>
            {modelSource ? (
              <span className="self-center text-xs text-slate-400">
                模型来源：{modelSource}
              </span>
            ) : null}
          </div>
          {modelTestResult ? (
            <div
              className={`mt-4 rounded-2xl border px-4 py-3 text-sm ${modelTestResult.ok ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100" : "border-amber-300/20 bg-amber-300/10 text-amber-100"}`}
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
          <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
            <h2 className="text-lg font-semibold">Key 会话</h2>
            <p className="mt-1 text-sm text-slate-400">
              {selectedKeyRecord
                ? `当前 Key：${selectedKeyRecord.label || selectedKeyRecord.key_id}`
                : "选择访问 Key 查看关联会话。"}
            </p>
            <div className="mt-4 space-y-2">
              {keySessions.map((item) => (
                <button
                  key={item.session_id}
                  onClick={() => void loadMessages(item.session_id)}
                  className="w-full rounded-2xl border border-white/10 bg-white/[0.055] px-4 py-3 text-left hover:bg-cyan-200/10"
                >
                  <div className="font-medium">{item.session_id}</div>
                  <div className="mt-1 text-xs text-slate-400">
                    {item.declared_family ?? "unknown"} · {item.message_count}{" "}
                    条消息 · {item.phase_state ?? "phase unknown"}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
            <h2 className="text-lg font-semibold">会话消息</h2>
            <div className="mt-4 max-h-[520px] space-y-2 overflow-auto rounded-2xl border border-white/10 bg-white/[0.045] p-3">
              {selectedMessages.length === 0 ? (
                <div className="p-4 text-sm text-slate-400">
                  选择一个会话后显示消息。
                </div>
              ) : null}
              {selectedMessages.map((message) => (
                <div
                  key={message.turn_id}
                  className="rounded-xl bg-white/[0.06] px-3 py-2 text-sm"
                >
                  <div className="text-xs font-medium text-cyan-200">
                    {message.role} · #{message.turn_index}
                  </div>
                  <div className="mt-1 whitespace-pre-wrap text-slate-200">
                    {message.content}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="knowledge" className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-lg shadow-black/25 backdrop-blur-xl">
          <h2 className="text-lg font-semibold">RAG / 知识库状态</h2>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <input
              type="file"
              onChange={(event) => setRagFile(event.target.files?.[0] ?? null)}
              className="rounded-2xl border border-white/10 bg-white/[0.055] px-3 py-2 text-sm"
            />
            <button
              className="rounded-2xl bg-white px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-white/10 transition hover:bg-cyan-50 disabled:opacity-50"
              onClick={() => void uploadRagFile()}
              disabled={!ragFile}
            >
              上传到知识库
            </button>
          </div>
          <pre className="mt-4 max-h-80 overflow-auto rounded-2xl bg-black/60 p-4 text-xs text-slate-100">
            {JSON.stringify(ragStatus, null, 2)}
          </pre>
        </section>
      </div>

      {createDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="w-full max-w-2xl rounded-[28px] border border-white/12 bg-[#07101f]/95 p-6 text-white shadow-2xl shadow-black/40">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold">创建客户访问 Key</h2>
                <p className="mt-1 text-sm text-slate-400">
                  默认有效期 30 天；创建前需要二次确认。
                </p>
              </div>
              <button
                className="rounded-xl border border-white/10 px-3 py-1 text-sm text-slate-200 transition hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
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
                    className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 font-normal text-white placeholder:text-slate-500 focus:border-cyan-200/40 focus:outline-none"
                    value={keyLabel}
                    onChange={(event) => setKeyLabel(event.target.value)}
                    placeholder="例如：客户 A / 6 月批次"
                  />
                </label>
                <label className="grid gap-2 text-sm font-medium">
                  会话额度
                  <input
                    className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 font-normal text-white placeholder:text-slate-500 focus:border-cyan-200/40 focus:outline-none"
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
                        className={`rounded-2xl border px-3 py-2 text-sm ${validityPreset === preset ? "border-blue-500 bg-cyan-200/10 text-cyan-200" : "border-white/10 bg-white/[0.08] text-slate-200"}`}
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
                      className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 font-normal text-white placeholder:text-slate-500 focus:border-cyan-200/40 focus:outline-none"
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
                <label className="flex items-center justify-between rounded-2xl border border-white/10 px-4 py-3 text-sm font-medium">
                  创建后立即启用
                  <input
                    type="checkbox"
                    checked={keyEnabled}
                    onChange={(event) => setKeyEnabled(event.target.checked)}
                  />
                </label>
                <div className="flex justify-end gap-2">
                  <button
                    className="rounded-2xl border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
                    onClick={() => {
                      setCreateDialogOpen(false)
                      resetCreateForm()
                    }}
                  >
                    取消
                  </button>
                  <button
                    className="rounded-2xl bg-cyan-200 px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-950/20 transition hover:bg-cyan-100"
                    onClick={() => setCreateStep("confirm")}
                  >
                    继续确认
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-5 space-y-4">
                <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-sm leading-6 text-amber-100">
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
                    className="rounded-2xl border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
                    onClick={() => setCreateStep("form")}
                  >
                    返回修改
                  </button>
                  <button
                    className="rounded-2xl bg-cyan-200 px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-950/20 transition hover:bg-cyan-100"
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-[28px] border border-white/12 bg-[#07101f]/95 p-6 text-white shadow-2xl shadow-black/40">
            <h2 className="text-xl font-semibold">
              确认{toggleTarget.nextEnabled ? "启用" : "停用"}访问 Key
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              目标：{toggleTarget.key.label || toggleTarget.key.key_id}。
              {toggleTarget.nextEnabled
                ? "启用后可继续创建新会话。"
                : "停用后不会删除历史会话，但不能再创建新会话。"}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-2xl border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
                onClick={() => setToggleTarget(null)}
              >
                取消
              </button>
              <button
                className="rounded-2xl bg-cyan-200 px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-950/20 transition hover:bg-cyan-100"
                onClick={() => void applyToggle()}
              >
                确认
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {quotaTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-[28px] border border-white/12 bg-[#07101f]/95 p-6 text-white shadow-2xl shadow-black/40">
            <h2 className="text-xl font-semibold">调整访问额度</h2>
            <p className="mt-2 text-sm text-slate-400">
              当前额度 {quotaTarget.key.usage_limit}，已使用{" "}
              {quotaTarget.key.usage_count}。
            </p>
            <div className="mt-4 grid gap-3">
              <select
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white"
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
                className="h-11 rounded-2xl border border-white/10 bg-white/[0.06] px-4 text-white"
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
              <div className="rounded-2xl border border-white/10 bg-white/[0.045] px-4 py-3 text-sm text-slate-300">
                确认后总额度将变为{" "}
                {quotaTarget.mode === "add"
                  ? quotaTarget.key.usage_limit + quotaTarget.value
                  : quotaTarget.value}
                。
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-2xl border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
                onClick={() => setQuotaTarget(null)}
              >
                取消
              </button>
              <button
                className="rounded-2xl bg-cyan-200 px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-950/20 transition hover:bg-cyan-100"
                onClick={() => void applyQuota()}
              >
                保存额度
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {materialCleanupTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-[28px] border border-red-300/20 bg-[#07101f]/95 p-6 text-white shadow-2xl shadow-black/40">
            <h2 className="text-xl font-semibold">确认清理该 Key 资料</h2>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              目标：{materialCleanupTarget.label || materialCleanupTarget.key_id}。
              此操作只会 tombstone 该访问 Key 名下所有会话里的非模板上传资料；
              不会删除 validated template / material package、会话、消息或访问 Key。
            </p>
            <div className="mt-4 rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-xs leading-5 text-amber-100">
              清理范围通过 access_key_sessions 限定，不会跨 Key 处理其它客户资料。
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-2xl border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
                onClick={() => setMaterialCleanupTarget(null)}
              >
                取消
              </button>
              <button
                className="rounded-2xl bg-red-200 px-4 py-2 text-sm font-semibold text-red-950 shadow-lg shadow-red-950/20 transition hover:bg-red-100"
                onClick={() => void applyMaterialCleanup()}
              >
                确认清理资料
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  )
}
