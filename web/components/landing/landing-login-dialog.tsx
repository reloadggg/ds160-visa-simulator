"use client"

import type { FormEvent } from "react"
import { useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRight, KeyRound, ShieldAlert } from "lucide-react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuth } from "@/hooks/use-auth"
import {
  maskAccessKeyForDisplay,
  parseSharedAccessKeyFromLocation,
  stripSharedAccessKeyFromCurrentUrl,
} from "@/lib/access-key-share"
import { cn } from "@/lib/utils"

type LandingLoginDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function LandingLoginDialog({ open, onOpenChange }: LandingLoginDialogProps) {
  const router = useRouter()
  const { login, isLoggingIn, error } = useAuth()
  const [localError, setLocalError] = useState<string | null>(null)
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
    if (isLoggingIn) {
      return
    }

    const formData = new FormData(event.currentTarget)
    const password = (
      sharedAccessKey ?? String(formData.get("password") ?? "")
    ).trim()

    if (!password) {
      setLocalError("请输入管理员发放的授权 Key")
      return
    }

    setLocalError(null)
    const ok = await login(password)
    if (ok) {
      if (sharedAccessKey) {
        stripSharedAccessKeyFromCurrentUrl()
        setSharedAccessKey(null)
      }
      onOpenChange(false)
      router.push("/login")
    }
  }

  const visibleError = localError ?? error

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          "overflow-hidden border-white/12 bg-[#050608]/95 p-0 text-white shadow-2xl shadow-cyan-950/40 backdrop-blur-2xl sm:max-w-[520px]",
          "rounded-[2rem] supports-[backdrop-filter]:bg-[#050608]/82",
        )}
      >
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_0%,rgba(125,211,252,0.20),transparent_34%),radial-gradient(circle_at_86%_18%,rgba(14,165,233,0.10),transparent_30%),linear-gradient(180deg,rgba(255,255,255,0.07),transparent_42%)]" />
        <div className="pointer-events-none absolute -right-16 -top-20 h-48 w-48 rounded-full bg-cyan-200/12 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 left-1/2 h-48 w-72 -translate-x-1/2 rounded-full bg-sky-300/8 blur-3xl" />

        <div className="relative z-10 border-b border-white/10 px-6 pb-5 pt-6 sm:px-7 sm:pt-7">
          <DialogClose className="absolute right-5 top-5 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-white/58 transition hover:border-white/25 hover:bg-white/[0.08] hover:text-white focus:outline-none focus:ring-2 focus:ring-cyan-200/40">
            关闭
          </DialogClose>

          <DialogHeader className="max-w-[90%] gap-3 text-left">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-[10px] bg-gradient-to-br from-sky-300 to-blue-600 text-[11px] font-extrabold text-[#001a33]">
                DS
              </div>
              <div className="inline-flex w-fit items-center gap-2 rounded-full border border-cyan-200/15 bg-cyan-200/[0.06] px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.18em] text-cyan-100/80">
                <span className="h-1.5 w-1.5 rounded-full bg-cyan-200 shadow-[0_0_16px_rgba(125,211,252,0.9)]" />
                授权访问
              </div>
            </div>
            <DialogTitle className="text-2xl font-semibold leading-tight tracking-tight text-white sm:text-3xl">
              DS-160 模拟面签
            </DialogTitle>
            <DialogDescription className="text-sm leading-6 text-slate-300">
              使用 Access Key 进入工作台。进入后可在设置中修改显示名。
            </DialogDescription>
          </DialogHeader>
        </div>

        <form onSubmit={handleSubmit} className="relative z-10 space-y-5 px-6 py-6 sm:px-7 sm:py-7">
          {visibleError ? (
            <Alert className="rounded-2xl border-red-300/20 bg-red-500/10 text-red-100">
              <ShieldAlert className="h-4 w-4" />
              <AlertDescription className="text-red-100/90">{visibleError}</AlertDescription>
            </Alert>
          ) : null}

          {hasSharedAccessKey ? (
            <div className="rounded-2xl border border-cyan-200/15 bg-cyan-200/[0.06] p-4 text-sm leading-6 text-cyan-50">
              <div className="flex items-center gap-2 font-semibold">
                <KeyRound className="h-4 w-4" />
                已识别分享链接中的授权 Key
              </div>
              <div className="mt-1 font-mono text-xs text-cyan-100/80">
                {maskedSharedAccessKey}
              </div>
              <p className="mt-2 text-xs text-slate-400">
                点击“启用并进入工作台”即可使用；验证成功后会清理地址栏中的 Key。
              </p>
            </div>
          ) : (
            <div className="space-y-2.5">
              <Label htmlFor="landing-login-password" className="text-sm font-semibold text-slate-200">
                授权 Key
                <span className="font-normal text-cyan-100/58">必填</span>
              </Label>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-white/34" />
                <Input
                  id="landing-login-password"
                  name="password"
                  type="password"
                  placeholder="ds160_..."
                  autoComplete="current-password"
                  required
                  autoFocus
                  disabled={isLoggingIn}
                  aria-invalid={Boolean(visibleError)}
                  className="h-12 rounded-2xl border-white/10 bg-white/[0.055] pl-11 pr-4 text-base text-white shadow-inner shadow-white/[0.03] placeholder:text-slate-500 focus-visible:border-cyan-200/40 focus-visible:ring-cyan-200/18 sm:h-13"
                />
              </div>
            </div>
          )}

          <button
            type="submit"
            disabled={isLoggingIn}
            className="flex h-12 w-full items-center justify-center gap-2 rounded-full bg-[#f5f5f7] px-4 text-base font-semibold text-slate-950 transition hover:bg-white disabled:opacity-60 sm:h-13"
          >
            {isLoggingIn
              ? "正在验证..."
              : hasSharedAccessKey
                ? "启用并进入工作台"
                : "进入工作台"}
            <ArrowRight className="h-4 w-4" />
          </button>

          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-xs leading-5 text-slate-400">
            授权 Key 只用于验证当前访问权限；通过后会进入模拟面签工作台，原有直接登录入口仍可作为备用入口使用。
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
