"use client"

import { FormEvent, useEffect, useState } from "react"
import { buildApiUrl } from "@/lib/api/config"

type AdminSettings = Record<string, unknown> & {
  show_github_link?: boolean
  debug_console_enabled?: boolean
  debug_material_enabled?: boolean
  user_model_config_enabled?: boolean
  rag_status_user_visible?: boolean
  model_base_url?: string | null
  model_name?: string | null
  model_streaming_enabled?: boolean
  model_api_key_configured?: boolean
}

type AccessKeyRecord = {
  key_id: string
  label: string
  usage_limit: number
  usage_count: number
  remaining_uses: number
  enabled: boolean
  can_create_session?: boolean
  created_at: string
  expires_at?: string | null
  last_used_at?: string | null
  revoked_at?: string | null
}

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

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
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
  const [keys, setKeys] = useState<AccessKeyRecord[]>([])
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [keyLabel, setKeyLabel] = useState("")
  const [usageLimit, setUsageLimit] = useState(1)
  const [keyExpiresAt, setKeyExpiresAt] = useState("")
  const [keyEnabled, setKeyEnabled] = useState(true)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [keySessions, setKeySessions] = useState<KeySession[]>([])
  const [selectedMessages, setSelectedMessages] = useState<AdminMessage[]>([])
  const [ragStatus, setRagStatus] = useState<Record<string, unknown> | null>(null)
  const [ragFile, setRagFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    const [settingsPayload, keysPayload, ragPayload] = await Promise.all([
      api<AdminSettings>("/v1/admin/settings"),
      api<{ keys: AccessKeyRecord[] }>("/v1/admin/access-keys"),
      api<Record<string, unknown>>("/v1/admin/rag/status").catch(() => null),
    ])
    setSettings(settingsPayload)
    setKeys(keysPayload.keys)
    setRagStatus(ragPayload)
  }

  useEffect(() => {
    api<{ authenticated: boolean }>("/v1/admin/me")
      .then((payload) => {
        setAuthenticated(payload.authenticated)
        if (payload.authenticated) void refresh()
      })
      .catch(() => setAuthenticated(false))
  }, [])

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    try {
      await api("/v1/admin/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      })
      setAuthenticated(true)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败")
    }
  }

  const createKey = async () => {
    setCreatedKey(null)
    const payload = await api<{ key: string; record: AccessKeyRecord }>("/v1/admin/access-keys", {
      method: "POST",
      body: JSON.stringify({
        label: keyLabel,
        usage_limit: usageLimit,
        expires_at: keyExpiresAt ? new Date(keyExpiresAt).toISOString() : null,
        enabled: keyEnabled,
      }),
    })
    setCreatedKey(payload.key)
    setKeyLabel("")
    setUsageLimit(1)
    setKeyExpiresAt("")
    setKeyEnabled(true)
    await refresh()
  }

  const updateKey = async (keyId: string, patch: Record<string, unknown>) => {
    await api<{ record: AccessKeyRecord }>(`/v1/admin/access-keys/${keyId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
    await refresh()
    if (selectedKey === keyId) {
      await loadKeySessions(keyId)
    }
  }

  const updateSettings = async (patch: Partial<AdminSettings>) => {
    const next = await api<AdminSettings>("/v1/admin/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
    setSettings(next)
  }

  const saveModelSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    await updateSettings({
      model_base_url: String(form.get("model_base_url") ?? ""),
      model_api_key: String(form.get("model_api_key") ?? "") || undefined,
      model_name: String(form.get("model_name") ?? ""),
      model_streaming_enabled: form.get("model_streaming_enabled") === "on",
    })
  }

  const loadKeySessions = async (keyId: string) => {
    setSelectedKey(keyId)
    setSelectedMessages([])
    const payload = await api<{ sessions: KeySession[] }>(`/v1/admin/access-keys/${keyId}/sessions`)
    setKeySessions(payload.sessions)
  }

  const loadMessages = async (sessionId: string) => {
    const payload = await api<{ messages: AdminMessage[] }>(`/v1/admin/sessions/${sessionId}/messages`)
    setSelectedMessages(payload.messages)
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
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "RAG 上传失败")
    }
  }

  if (!authenticated) {
    return (
      <main className="min-h-screen bg-[radial-gradient(circle_at_20%_10%,rgba(37,99,235,.18),transparent_32%),linear-gradient(135deg,#f8fbff,#eaf2ff)] p-6 text-slate-950">
        <form onSubmit={handleLogin} className="mx-auto mt-24 max-w-md rounded-[28px] border border-white/70 bg-white/70 p-8 shadow-2xl shadow-blue-950/10 backdrop-blur-xl">
          <h1 className="text-2xl font-semibold">后台管理</h1>
          <p className="mt-2 text-sm text-slate-500">使用当前管理员密码进入 demo 控制台。</p>
          <input className="mt-6 h-12 w-full rounded-2xl border border-slate-200 bg-white/80 px-4" type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="管理员密码" />
          {error ? <div className="mt-3 text-sm text-red-600">{error}</div> : null}
          <button className="mt-5 h-12 w-full rounded-2xl bg-blue-600 font-semibold text-white shadow-lg shadow-blue-600/20 hover:bg-blue-700" type="submit">进入后台</button>
        </form>
      </main>
    )
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_15%_10%,rgba(37,99,235,.16),transparent_30%),radial-gradient(circle_at_85%_0%,rgba(14,165,233,.14),transparent_28%),linear-gradient(135deg,#f8fbff,#edf4ff)] p-5 text-slate-950">
      <div className="mx-auto max-w-7xl space-y-5">
        <header className="rounded-[28px] border border-white/70 bg-white/70 p-6 shadow-xl shadow-blue-950/10 backdrop-blur-xl">
          <h1 className="text-2xl font-semibold">DS-160 Demo 后台</h1>
          <p className="mt-1 text-sm text-slate-500">管理访问 key、模型参数、RAG 状态、调试台和前端展示。</p>
          {error ? <div className="mt-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">{error}</div> : null}
        </header>

        <section className="grid gap-5 lg:grid-cols-[1fr_1fr]">
          <div className="rounded-[24px] border border-white/70 bg-white/72 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <h2 className="font-semibold">创建访问 Key</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_120px_220px_auto_auto]">
              <input className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4" value={keyLabel} onChange={(event) => setKeyLabel(event.target.value)} placeholder="备注，例如客户 A" />
              <input className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4" type="number" min={1} value={usageLimit} onChange={(event) => setUsageLimit(Number(event.target.value) || 1)} />
              <input className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4" type="datetime-local" value={keyExpiresAt} onChange={(event) => setKeyExpiresAt(event.target.value)} title="可选过期时间" />
              <label className="flex h-11 items-center gap-2 rounded-2xl border border-slate-200 bg-white/70 px-3 text-sm">
                <input type="checkbox" checked={keyEnabled} onChange={(event) => setKeyEnabled(event.target.checked)} />
                启用
              </label>
              <button className="rounded-2xl bg-blue-600 px-5 font-semibold text-white" onClick={createKey}>生成</button>
            </div>
            {createdKey ? <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm"><div className="font-medium">只显示一次：</div><code className="break-all">{createdKey}</code></div> : null}
          </div>

          <div className="rounded-[24px] border border-white/70 bg-white/72 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <h2 className="font-semibold">功能开关</h2>
            <div className="mt-4 grid gap-3 text-sm">
              {[
                ["show_github_link", "显示 GitHub 信息"],
                ["debug_console_enabled", "开放调试台"],
                ["debug_material_enabled", "开放调试材料"],
                ["user_model_config_enabled", "允许用户自定义模型"],
                ["rag_status_user_visible", "用户可见 RAG 状态"],
              ].map(([key, label]) => (
                <label key={key} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white/60 px-4 py-3">
                  <span>{label}</span>
                  <input type="checkbox" checked={Boolean(settings?.[key])} onChange={(event) => updateSettings({ [key]: event.target.checked })} />
                </label>
              ))}
            </div>
          </div>
        </section>

        <section className="rounded-[24px] border border-white/70 bg-white/72 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
          <h2 className="font-semibold">后台模型配置</h2>
          <p className="mt-1 text-sm text-slate-500">用户侧默认不能自定义 Base URL；demo 使用这里保存的模型参数。</p>
          <form onSubmit={saveModelSettings} className="mt-4 grid gap-3 lg:grid-cols-[1.2fr_1fr_1fr_auto]">
            <input name="model_base_url" defaultValue={settings?.model_base_url ?? ""} className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4" placeholder="Base URL，例如 https://.../v1" />
            <input name="model_api_key" type="password" className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4" placeholder={settings?.model_api_key_configured ? "已配置；留空不修改" : "API Key"} />
            <input name="model_name" defaultValue={settings?.model_name ?? ""} className="h-11 rounded-2xl border border-slate-200 bg-white/80 px-4" placeholder="模型名称" />
            <label className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/60 px-4 text-sm">
              <input name="model_streaming_enabled" type="checkbox" defaultChecked={settings?.model_streaming_enabled !== false} />
              流式
            </label>
            <button className="h-11 rounded-2xl bg-blue-600 px-5 font-semibold text-white lg:col-start-4" type="submit">保存模型</button>
          </form>
        </section>

        <section className="grid gap-5 lg:grid-cols-[0.95fr_1.05fr]">
          <div className="rounded-[24px] border border-white/70 bg-white/72 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <h2 className="font-semibold">Key 列表</h2>
            <div className="mt-4 space-y-2">
              {keys.map((item) => (
                <div key={item.key_id} className="rounded-2xl border border-slate-200 bg-white/65 px-4 py-3 hover:bg-blue-50">
                  <button onClick={() => loadKeySessions(item.key_id)} className="w-full text-left">
                    <div className="flex justify-between gap-3"><span className="font-medium">{item.label || item.key_id}</span><span>{item.usage_count}/{item.usage_limit}</span></div>
                    <div className="mt-1 text-xs text-slate-500">
                      剩余 {item.remaining_uses} 次 · {item.enabled ? "启用" : "停用"}
                      {item.expires_at ? ` · 过期 ${new Date(item.expires_at).toLocaleString()}` : ""}
                    </div>
                  </button>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <button className="rounded-xl border border-slate-200 bg-white/80 px-3 py-1.5 text-slate-700" onClick={() => updateKey(item.key_id, { enabled: !item.enabled })}>
                      {item.enabled ? "停用" : "启用"}
                    </button>
                    <button className="rounded-xl border border-slate-200 bg-white/80 px-3 py-1.5 text-slate-700" onClick={() => updateKey(item.key_id, { usage_limit: item.usage_limit + 1 })}>
                      额度 +1
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[24px] border border-white/70 bg-white/72 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
            <h2 className="font-semibold">Key 会话与历史</h2>
            <p className="mt-1 text-sm text-slate-500">{selectedKey ? `当前 key：${selectedKey}` : "选择左侧 key 查看会话。"}</p>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              <div className="space-y-2">
                {keySessions.map((item) => (
                  <button key={item.session_id} onClick={() => loadMessages(item.session_id)} className="w-full rounded-2xl border border-slate-200 bg-white/65 px-4 py-3 text-left hover:bg-blue-50">
                    <div className="font-medium">{item.session_id}</div>
                    <div className="mt-1 text-xs text-slate-500">{item.declared_family ?? "unknown"} · {item.message_count} 条消息</div>
                  </button>
                ))}
              </div>
              <div className="max-h-[520px] space-y-2 overflow-auto rounded-2xl border border-slate-200 bg-white/50 p-3">
                {selectedMessages.map((message) => (
                  <div key={message.turn_id} className="rounded-xl bg-white/80 px-3 py-2 text-sm">
                    <div className="text-xs font-medium text-blue-700">{message.role} · #{message.turn_index}</div>
                    <div className="mt-1 whitespace-pre-wrap text-slate-700">{message.content}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-[24px] border border-white/70 bg-white/72 p-5 shadow-lg shadow-blue-950/10 backdrop-blur-xl">
          <h2 className="font-semibold">RAG / 知识库状态</h2>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <input type="file" onChange={(event) => setRagFile(event.target.files?.[0] ?? null)} className="rounded-2xl border border-slate-200 bg-white/70 px-3 py-2 text-sm" />
            <button className="rounded-2xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" onClick={uploadRagFile} disabled={!ragFile}>
              上传到知识库
            </button>
          </div>
          <pre className="mt-4 max-h-80 overflow-auto rounded-2xl bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(ragStatus, null, 2)}</pre>
        </section>
      </div>
    </main>
  )
}
