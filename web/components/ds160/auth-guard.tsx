"use client"

import type { FormEvent, ReactNode } from "react"
import { useAuth } from "@/hooks/use-auth"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { ArrowRight, LockKeyhole, ShieldAlert, Sparkles } from "lucide-react"

interface AuthGuardProps {
  children: ReactNode
}

const previewPoints = ["真实面签节奏", "材料与风险联动", "Agent 2.0 测试环境"]

export function AuthGuard({ children }: AuthGuardProps) {
  const { isAuthenticated, isLoggingIn, error, login } = useAuth()

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const formData = new FormData(event.currentTarget)
    const password = String(formData.get("password") ?? "").trim()
    const displayName = String(formData.get("displayName") ?? "").trim()
    if (!password || isLoggingIn) {
      return
    }
    await login(password, displayName)
  }

  if (isAuthenticated) {
    return <>{children}</>
  }

  return (
    <main className="relative min-h-[100dvh] overflow-hidden bg-[#f6f7fb] text-slate-950">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_18%,rgba(37,99,235,0.14),transparent_30%),radial-gradient(circle_at_82%_12%,rgba(14,165,233,0.16),transparent_28%),linear-gradient(135deg,rgba(255,255,255,0.9),rgba(226,232,240,0.58))]" />
      <div className="absolute left-1/2 top-8 h-64 w-64 -translate-x-1/2 rounded-full bg-white/60 blur-3xl" />

      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-6xl items-center px-4 py-6 sm:px-6 md:px-8">
        <section className="grid w-full overflow-hidden rounded-[1.5rem] border border-white/70 bg-white/72 shadow-2xl shadow-slate-950/10 backdrop-blur-xl sm:rounded-[2rem] lg:grid-cols-[1.05fr_0.95fr]">
          <div className="relative hidden min-h-[620px] flex-col justify-between overflow-hidden bg-slate-950 p-10 text-white lg:flex">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(56,189,248,0.34),transparent_28%),radial-gradient(circle_at_80%_0%,rgba(96,165,250,0.24),transparent_24%),linear-gradient(145deg,#020617,#0f172a_62%,#111827)]" />
            <div className="absolute inset-x-8 top-28 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent" />
            <div className="absolute bottom-10 right-10 h-40 w-40 rounded-full border border-white/10" />

            <div className="relative z-10 space-y-8">
              <div className="inline-flex items-center gap-2 rounded-full border border-white/12 bg-white/8 px-3 py-1 text-xs font-medium text-sky-100 backdrop-blur">
                <Sparkles className="h-3.5 w-3.5" />
                Agent 2.0 Private Preview
              </div>

              <div className="space-y-5">
                <p className="text-sm uppercase tracking-[0.42em] text-sky-200/80">DS-160 Workbench</p>
                <h1 className="max-w-xl text-5xl font-semibold tracking-[-0.04em] text-white">
                  面签模拟，不只是聊天。
                </h1>
                <p className="max-w-md text-base leading-7 text-slate-300">
                  当前版本用于内部联调：围绕签证类型、材料、风险点和追问策略构建完整的面签工作台。
                </p>
              </div>
            </div>

            <div className="relative z-10 grid gap-3">
              {previewPoints.map((point, index) => (
                <div
                  key={point}
                  className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/[0.06] px-4 py-3 backdrop-blur"
                >
                  <span className="text-sm text-slate-200">{point}</span>
                  <span className="text-xs font-medium text-sky-200">0{index + 1}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="flex min-h-[500px] items-center justify-center p-6 sm:min-h-[620px] sm:p-10">
            <div className="w-full max-w-md space-y-6 sm:space-y-8">
              <div className="space-y-4 lg:hidden">
                <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 shadow-sm">
                  <Sparkles className="h-3.5 w-3.5 text-sky-500" />
                  Agent 2.0 Private Preview
                </div>
                <div>
                  <p className="text-sm uppercase tracking-[0.32em] text-slate-500">DS-160 Workbench</p>
                  <h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] text-slate-950 sm:text-4xl">
                    面签模拟工作台
                  </h1>
                </div>
              </div>

              <div className="space-y-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-white shadow-lg shadow-slate-950/20">
                  <LockKeyhole className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-2xl font-semibold tracking-[-0.03em] text-slate-950 sm:text-3xl">进入测试环境</h2>
                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    请输入访问密码。验证通过后会进入 DS-160 面签工作台。
                  </p>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-5">
                {error ? (
                  <Alert variant="destructive" className="rounded-2xl border-red-200 bg-red-50 text-red-900">
                    <ShieldAlert className="h-4 w-4" />
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                ) : null}

                <div className="space-y-2">
                  <Label htmlFor="auth-display-name" className="text-sm font-medium text-slate-700">
                    用户名
                  </Label>
                  <Input
                    id="auth-display-name"
                    name="displayName"
                    type="text"
                    placeholder="不填则自动生成 User_123456"
                    autoComplete="nickname"
                    disabled={isLoggingIn}
                    className={cn(
                      "h-12 rounded-2xl border-slate-200 bg-white/80 px-4 text-base shadow-sm transition-all sm:h-13",
                      "placeholder:text-slate-400 focus-visible:border-sky-400/20 focus-visible:ring-sky-400/20",
                    )}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="auth-password" className="text-sm font-medium text-slate-700">
                    访问密码
                  </Label>
                  <Input
                    id="auth-password"
                    name="password"
                    type="password"
                    placeholder="agent2-..."
                    autoComplete="current-password"
                    autoFocus
                    required
                    disabled={isLoggingIn}
                    className={cn(
                      "h-12 rounded-2xl border-slate-200 bg-white/80 px-4 text-base shadow-sm transition-all sm:h-13",
                      "placeholder:text-slate-400 focus-visible:border-sky-400 focus-visible:ring-sky-400/20",
                    )}
                  />
                </div>

                <button
                  type="submit"
                  disabled={isLoggingIn}
                  className="flex h-12 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 text-base font-semibold text-white shadow-lg shadow-slate-950/18 transition-all hover:-translate-y-0.5 hover:bg-slate-800 disabled:opacity-50 sm:h-13"
                >
                  {isLoggingIn ? "正在验证..." : "确认进入"}
                  <ArrowRight className="h-4 w-4" />
                </button>
              </form>

              <div className="rounded-2xl border border-slate-200 bg-white/70 p-4 text-xs leading-5 text-slate-500">
                这是临时公网测试入口。不要公开分享链接或密码；如页面无响应，请刷新后重试。
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}
