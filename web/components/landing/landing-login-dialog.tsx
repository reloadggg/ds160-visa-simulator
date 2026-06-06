"use client"

import type { FormEvent } from "react"
import { useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRight, KeyRound, ShieldAlert, Sparkles, UserRound } from "lucide-react"

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
import { cn } from "@/lib/utils"

type LandingLoginDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function LandingLoginDialog({ open, onOpenChange }: LandingLoginDialogProps) {
  const router = useRouter()
  const { login, isLoggingIn, error } = useAuth()
  const [localError, setLocalError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isLoggingIn) {
      return
    }

    const formData = new FormData(event.currentTarget)
    const displayName = String(formData.get("displayName") ?? "").trim()
    const password = String(formData.get("password") ?? "").trim()

    if (!password) {
      setLocalError("请输入后台发放的授权 Key")
      return
    }

    setLocalError(null)
    const ok = await login(password, displayName)
    if (ok) {
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
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_0%,rgba(125,211,252,0.20),transparent_34%),radial-gradient(circle_at_86%_18%,rgba(168,85,247,0.14),transparent_30%),linear-gradient(180deg,rgba(255,255,255,0.07),transparent_42%)]" />
        <div className="pointer-events-none absolute -right-16 -top-20 h-48 w-48 rounded-full bg-cyan-200/12 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 left-1/2 h-48 w-72 -translate-x-1/2 rounded-full bg-violet-300/10 blur-3xl" />

        <div className="relative z-10 border-b border-white/10 px-6 pb-5 pt-6 sm:px-7 sm:pt-7">
          <DialogClose className="absolute right-5 top-5 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-white/58 transition hover:border-white/25 hover:bg-white/[0.08] hover:text-white focus:outline-none focus:ring-2 focus:ring-cyan-200/40">
            关闭
          </DialogClose>

          <DialogHeader className="max-w-[82%] gap-3 text-left">
            <div className="inline-flex w-fit items-center gap-2 rounded-full border border-cyan-200/15 bg-cyan-200/[0.06] px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.18em] text-cyan-100/80">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-200 shadow-[0_0_16px_rgba(125,211,252,0.9)]" />
              Secure access
            </div>
            <DialogTitle
              className={cn(
                "text-3xl font-black leading-tight tracking-[0.015em] text-white sm:text-4xl",
                "[font-family:'Arial_Rounded_MT_Bold','Trebuchet_MS','Avenir_Next','Inter','PingFang_SC','Microsoft_YaHei_UI',system-ui,sans-serif]",
              )}
            >
              进入模拟面签
            </DialogTitle>
            <DialogDescription className="text-sm leading-6 text-slate-300">
              使用后台发放的授权 Key 解锁工作台。用户名可留空，系统会自动生成一个临时身份。
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

          <div className="space-y-2.5">
            <Label htmlFor="landing-login-display-name" className="text-sm font-semibold text-slate-200">
              用户名
              <span className="font-normal text-slate-500">可选</span>
            </Label>
            <div className="relative">
              <UserRound className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-white/34" />
              <Input
                id="landing-login-display-name"
                name="displayName"
                type="text"
                placeholder="不填则自动生成 User_123456"
                autoComplete="nickname"
                disabled={isLoggingIn}
                className="h-12 rounded-2xl border-white/10 bg-white/[0.055] pl-11 pr-4 text-base text-white shadow-inner shadow-white/[0.03] placeholder:text-slate-500 focus-visible:border-cyan-200/40 focus-visible:ring-cyan-200/18 sm:h-13"
              />
            </div>
          </div>

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

          <button
            type="submit"
            disabled={isLoggingIn}
            className="group flex h-12 w-full items-center justify-between rounded-2xl bg-white px-4 text-base font-black text-black shadow-2xl shadow-cyan-200/10 transition hover:-translate-y-0.5 hover:bg-cyan-50 disabled:pointer-events-none disabled:translate-y-0 disabled:opacity-60 sm:h-13"
          >
            <span className="inline-flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              {isLoggingIn ? "正在验证授权..." : "验证并进入工作台"}
            </span>
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-black text-white transition group-hover:translate-x-0.5">
              <ArrowRight className="h-4 w-4" />
            </span>
          </button>

          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-xs leading-5 text-slate-400">
            授权 Key 只用于验证当前访问权限；通过后会进入现有模拟面签工作台，原有直接登录入口仍可作为备用入口使用。
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
