"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Clock,
  Database,
  Github,
  Radio,
  Server,
  Sparkles,
} from "lucide-react"

import { getAppConfig } from "@/lib/api/client"
import { buildApiUrl } from "@/lib/api/config"
import { PROJECT_INFO } from "@/lib/project-info"
import type { AppConfig } from "@/lib/api/types"
import { cn } from "@/lib/utils"

type HealthPayload = {
  status?: string
  checks?: Record<string, Record<string, unknown>>
}

type HealthState =
  | { status: "loading"; payload: null; error: null; checkedAt: null }
  | { status: "ready"; payload: HealthPayload; error: null; checkedAt: Date }
  | { status: "error"; payload: null; error: string; checkedAt: Date }

const DEFAULT_APP_CONFIG: AppConfig = {
  show_github_link: false,
  debug_console_enabled: false,
  debug_material_enabled: false,
  user_model_config_enabled: false,
  rag_status_user_visible: false,
}

const statusTone = {
  ok: "border-emerald-300/20 bg-emerald-300/10 text-emerald-100",
  configured: "border-emerald-300/20 bg-emerald-300/10 text-emerald-100",
  running: "border-emerald-300/20 bg-emerald-300/10 text-emerald-100",
  degraded: "border-amber-300/20 bg-amber-300/10 text-amber-100",
  disabled: "border-slate-300/15 bg-white/[0.06] text-slate-200",
  not_configured: "border-amber-300/20 bg-amber-300/10 text-amber-100",
  not_started: "border-rose-300/20 bg-rose-300/10 text-rose-100",
  stopped: "border-rose-300/20 bg-rose-300/10 text-rose-100",
  error: "border-rose-300/20 bg-rose-300/10 text-rose-100",
} as const

const checkMeta = {
  app: {
    label: "应用服务",
    description: "版本、运行时与应用进程状态。",
    icon: Server,
  },
  database: {
    label: "数据库",
    description: "执行轻量查询，确认数据层可访问。",
    icon: Database,
  },
  llm: {
    label: "模型配置",
    description: "检查模型供应商、Base URL 与 API Key 配置状态。",
    icon: Sparkles,
  },
  worker: {
    label: "材料解析 Worker",
    description: "确认后台材料解析任务是否已启动并运行。",
    icon: Radio,
  },
} as const

const fallbackChecks = ["app", "database", "llm", "worker"] as const

export default function HealthPage() {
  const [state, setState] = useState<HealthState>({
    status: "loading",
    payload: null,
    error: null,
    checkedAt: null,
  })
  const [appConfig, setAppConfig] = useState<AppConfig>(DEFAULT_APP_CONFIG)

  useEffect(() => {
    let cancelled = false

    async function loadHealth() {
      try {
        const response = await fetch(buildApiUrl("/healthz"), {
          cache: "no-store",
        })
        const contentType = response.headers.get("content-type") ?? ""
        if (!contentType.includes("application/json")) {
          throw new Error("健康接口暂不可用，未返回有效状态数据")
        }
        const payload = (await response.json()) as HealthPayload
        if (!cancelled) {
          setState({
            status: "ready",
            payload,
            error: null,
            checkedAt: new Date(),
          })
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            status: "error",
            payload: null,
            error:
              error instanceof Error
                ? error.message
                : "健康接口暂不可用，无法读取项目状态",
            checkedAt: new Date(),
          })
        }
      }
    }

    void loadHealth()
    getAppConfig()
      .then((config) => {
        if (!cancelled) setAppConfig(config)
      })
      .catch(() => {
        if (!cancelled) setAppConfig(DEFAULT_APP_CONFIG)
      })
    const timer = window.setInterval(loadHealth, 30_000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const topStatus = state.status === "ready" ? state.payload.status ?? "unknown" : state.status
  const healthLabel = topStatus === "ok" ? "状态健康" : topStatus === "loading" ? "正在检查" : "需要关注"
  const checkedAtLabel = state.checkedAt
    ? state.checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "等待首次结果"

  const checkEntries = useMemo(() => {
    if (state.status !== "ready") {
      return fallbackChecks.map((key) => [key, { status: state.status }] as const)
    }
    const checks = state.payload.checks ?? {}
    return fallbackChecks.map((key) => [key, checks[key] ?? { status: "unknown" }] as const)
  }, [state])

  return (
    <main className="min-h-[100dvh] overflow-hidden bg-[#050608] text-white">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_16%_12%,rgba(16,185,129,0.20),transparent_28%),radial-gradient(circle_at_82%_10%,rgba(59,130,246,0.16),transparent_30%),linear-gradient(180deg,rgba(255,255,255,0.05),transparent_34%)]" />
      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-7xl flex-col px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex items-center justify-between rounded-full border border-white/10 bg-black/35 px-4 py-3 shadow-2xl shadow-black/20 backdrop-blur-2xl md:px-5">
          <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold text-white/72 transition hover:text-white">
            <ArrowLeft className="h-4 w-4" />
            返回首页
          </Link>
          <div className="hidden font-mono text-[11px] uppercase tracking-[0.22em] text-white/40 sm:block">
            System health
          </div>
          {appConfig.show_github_link ? (
            <a
              href={PROJECT_INFO.githubUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-full border border-white/10 px-3 py-2 text-sm text-white/60 transition hover:border-white/25 hover:text-white"
            >
              <Github className="h-4 w-4" />
              GitHub
            </a>
          ) : null}
        </header>

        <section className="grid flex-1 items-center gap-6 py-10 lg:grid-cols-[0.95fr_1.05fr] lg:py-14">
          <div className="relative overflow-hidden rounded-[2.35rem] border border-white/10 bg-white/[0.035] p-6 shadow-2xl shadow-black/35 backdrop-blur-2xl sm:p-8">
            <div
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute -right-8 -top-7 select-none text-[6rem] font-black leading-none tracking-[0.08em] text-white/[0.035] sm:text-[8rem]",
                "[font-family:'Arial_Rounded_MT_Bold','Trebuchet_MS','Avenir_Next','Inter','PingFang_SC','Microsoft_YaHei_UI',system-ui,sans-serif]",
              )}
            >
              HEALTH
            </div>

            <div className={cn("relative z-10 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold", toneFor(topStatus))}>
              {topStatus === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <CircleAlert className="h-4 w-4" />}
              {healthLabel}
            </div>

            <h1
              className={cn(
                "relative z-10 mt-7 text-5xl font-black leading-[0.95] tracking-[0.04em] text-white sm:text-6xl lg:text-7xl",
                "[font-family:'Arial_Rounded_MT_Bold','Trebuchet_MS','Avenir_Next','Inter','PingFang_SC','Microsoft_YaHei_UI',system-ui,sans-serif]",
              )}
            >
              项目状态，
              <span className="block bg-gradient-to-r from-white via-emerald-100 to-cyan-200 bg-clip-text text-transparent">
                实时可见
              </span>
            </h1>

            <p className="relative z-10 mt-6 max-w-xl text-base leading-8 text-slate-300">
              这里展示 DS-160 模拟面签系统的应用、数据库、模型配置和后台 Worker 状态。页面会定时刷新，方便演示前快速确认系统是否可用。
            </p>

            <div className="relative z-10 mt-8 grid gap-3 sm:grid-cols-3">
              <MetricCard label="整体状态" value={String(topStatus).toUpperCase()} />
              <MetricCard label="检查时间" value={checkedAtLabel} />
              <MetricCard label="刷新频率" value="30S" />
            </div>
          </div>

          <div className="grid gap-4">
            {state.status === "error" ? (
              <div className="rounded-[1.75rem] border border-rose-300/20 bg-rose-300/10 p-5 text-rose-50">
                <div className="flex items-center gap-2 font-semibold">
                  <CircleAlert className="h-5 w-5" />
                  无法读取后端健康状态
                </div>
                <p className="mt-2 text-sm leading-6 text-rose-50/70">
                  {state.error}。请确认 API 服务已启动，或检查前端代理配置是否指向正确后端。
                </p>
              </div>
            ) : null}

            <div className="grid gap-4 md:grid-cols-2">
              {checkEntries.map(([key, check]) => (
                <HealthCheckCard key={key} id={key} data={check} />
              ))}
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}

function HealthCheckCard({ id, data }: { id: string; data: Record<string, unknown> }) {
  const meta = checkMeta[id as keyof typeof checkMeta] ?? {
    label: id,
    description: "系统检查项。",
    icon: Activity,
  }
  const Icon = meta.icon
  const status = String(data.status ?? "unknown")
  const details = Object.entries(data).filter(([key]) => key !== "status")

  return (
    <article className="rounded-[1.75rem] border border-white/10 bg-white/[0.035] p-5 shadow-xl shadow-black/20 backdrop-blur-xl">
      <div className="flex items-start justify-between gap-4">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-black/25">
          <Icon className="h-5 w-5 text-cyan-100" />
        </div>
        <span className={cn("rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-[0.16em]", toneFor(status))}>
          {status}
        </span>
      </div>
      <h2 className="mt-5 text-xl font-semibold tracking-[-0.04em] text-white">{meta.label}</h2>
      <p className="mt-2 text-sm leading-6 text-white/52">{meta.description}</p>
      {details.length > 0 ? (
        <dl className="mt-5 grid gap-2">
          {details.slice(0, 5).map(([key, value]) => (
            <div key={key} className="flex items-center justify-between gap-3 rounded-2xl border border-white/8 bg-black/20 px-3 py-2">
              <dt className="text-xs text-white/38">{formatKey(key)}</dt>
              <dd className="max-w-[12rem] truncate font-mono text-xs text-white/70">{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </article>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="text-xs text-white/42">{label}</div>
      <div className="mt-3 flex items-center gap-2 font-mono text-xs uppercase tracking-[0.14em] text-emerald-100">
        <Clock className="h-3.5 w-3.5" />
        {value}
      </div>
    </div>
  )
}

function toneFor(status: string): string {
  return statusTone[status as keyof typeof statusTone] ?? "border-white/10 bg-white/[0.06] text-white/70"
}

function formatKey(value: string): string {
  return value.replace(/_/g, " ")
}

function formatValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false"
  if (value == null) return "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}
