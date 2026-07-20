"use client"

import type { FormEvent, ReactNode } from "react"
import { useState } from "react"
import { useAuth } from "@/hooks/use-auth"
import {
  maskAccessKeyForDisplay,
  parseSharedAccessKeyFromLocation,
  stripSharedAccessKeyFromCurrentUrl,
} from "@/lib/access-key-share"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { ArrowRight, KeyRound, LockKeyhole, ShieldAlert } from "lucide-react"

interface AuthGuardProps {
  children: ReactNode
}

function BrandMark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex h-10 w-10 items-center justify-center rounded-[10px] bg-gradient-to-br from-sky-300 to-blue-600 text-[11px] font-extrabold tracking-tight text-[#001a33] shadow-lg shadow-cyan-950/40",
        className,
      )}
    >
      DS
    </div>
  )
}

export function AuthGuard({ children }: AuthGuardProps) {
  const { isAuthenticated, isCheckingAuth, isLoggingIn, error, login } =
    useAuth()
  const [sharedAccessKey, setSharedAccessKey] = useState<string | null>(() => {
    if (typeof window === "undefined") {
      return null
    }
    return parseSharedAccessKeyFromLocation(window.location)
  })

  const hasSharedAccessKey = Boolean(sharedAccessKey)
  const maskedSharedAccessKey = sharedAccessKey
    ? maskAccessKeyForDisplay(sharedAccessKey)
    : ""

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const formData = new FormData(event.currentTarget)
    const password = (
      sharedAccessKey ?? String(formData.get("password") ?? "")
    ).trim()
    if (!password || isLoggingIn) {
      return
    }
    const ok = await login(password)
    if (ok && sharedAccessKey) {
      stripSharedAccessKeyFromCurrentUrl()
      setSharedAccessKey(null)
    }
  }

  if (isAuthenticated) {
    return <>{children}</>
  }

  if (isCheckingAuth) {
    return (
      <main className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-[#050608] text-white">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_-10%,rgba(56,189,248,0.18),transparent_50%),radial-gradient(circle_at_90%_0%,rgba(14,165,233,0.1),transparent_45%)]" />
        <div className="relative flex items-center gap-3 text-sm font-medium text-slate-300">
          <LockKeyhole className="h-4 w-4 text-cyan-200" />
          正在验证访问状态...
        </div>
      </main>
    )
  }

  return (
    <main className="relative min-h-[100dvh] overflow-hidden bg-[#050608] text-white">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(900px_480px_at_20%_-15%,rgba(56,189,248,0.2),transparent_55%),radial-gradient(700px_400px_at_90%_10%,rgba(14,165,233,0.1),transparent_50%)]" />
      <div className="pointer-events-none absolute -right-20 top-0 h-64 w-64 rounded-full bg-cyan-300/10 blur-3xl" />

      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-lg items-center px-4 py-10 sm:px-6">
        <section
          className={cn(
            "w-full overflow-hidden rounded-[1.5rem] border border-white/12",
            "bg-[#050608]/90 p-6 shadow-2xl shadow-cyan-950/40 backdrop-blur-2xl sm:rounded-[1.75rem] sm:p-8",
            "supports-[backdrop-filter]:bg-[#050608]/78",
          )}
        >
          <div className="mb-6 flex items-start gap-3">
            <BrandMark />
            <div className="min-w-0 pt-0.5">
              <h1 className="text-xl font-semibold tracking-tight text-white sm:text-2xl">
                DS-160 模拟面签
              </h1>
              <p className="mt-1 text-sm leading-6 text-slate-400">
                使用 Access Key 进入工作台
              </p>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error ? (
              <Alert className="rounded-2xl border-red-400/25 bg-red-500/10 text-red-100">
                <ShieldAlert className="h-4 w-4" />
                <AlertDescription className="text-red-100/90">
                  {error}
                </AlertDescription>
              </Alert>
            ) : null}

            {hasSharedAccessKey ? (
              <div className="rounded-2xl border border-cyan-200/15 bg-cyan-200/[0.08] p-4 text-sm leading-6 text-cyan-50">
                <div className="flex items-center gap-2 font-semibold">
                  <KeyRound className="h-4 w-4 text-cyan-200" />
                  已识别分享链接中的授权 Key
                </div>
                <div className="mt-1 font-mono text-xs text-cyan-100/80">
                  {maskedSharedAccessKey}
                </div>
                <p className="mt-2 text-xs text-slate-300">
                  点击下方按钮即可启用并进入工作台；验证成功后会自动清理地址栏中的
                  Key。
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <Label
                  htmlFor="auth-password"
                  className="text-sm font-medium text-slate-300"
                >
                  Access Key
                </Label>
                <Input
                  id="auth-password"
                  name="password"
                  type="password"
                  placeholder="粘贴或输入密钥"
                  autoComplete="current-password"
                  autoFocus
                  required
                  disabled={isLoggingIn}
                  className={cn(
                    "h-12 rounded-xl border-white/12 bg-black/35 px-4 text-base text-white shadow-none",
                    "placeholder:text-slate-500 focus-visible:border-cyan-300/50 focus-visible:ring-cyan-300/20",
                  )}
                />
              </div>
            )}

            <button
              type="submit"
              disabled={isLoggingIn}
              className={cn(
                "flex h-12 w-full items-center justify-center gap-2 rounded-full",
                "bg-[#f5f5f7] px-4 text-base font-semibold text-slate-950",
                "transition hover:bg-white disabled:opacity-50",
              )}
            >
              {isLoggingIn
                ? "正在验证..."
                : hasSharedAccessKey
                  ? "启用分享 Key 并进入"
                  : "进入工作台"}
              <ArrowRight className="h-4 w-4" />
            </button>
          </form>

          <p className="mt-5 text-xs leading-5 text-slate-500">
            授权 Key 由管理员发放并限定额度。工作台内可切换浅色 / 深色主题。
          </p>
        </section>
      </div>
    </main>
  )
}
