"use client"

import { FormEvent, useState } from "react"
import { KeyRound, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  maskAccessKeyForDisplay,
  parseSharedAccessKeyFromLocation,
  stripSharedAccessKeyFromCurrentUrl,
} from "@/lib/access-key-share"

interface WxAuthScreenProps {
  isChecking: boolean
  isLoggingIn: boolean
  error?: string | null
  onLogin: (accessKey: string) => Promise<boolean>
}

export function WxAuthScreen({
  isChecking,
  isLoggingIn,
  error,
  onLogin,
}: WxAuthScreenProps) {
  const [accessKey, setAccessKey] = useState("")
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
    const nextKey = (sharedAccessKey ?? accessKey).trim()
    if (!nextKey) {
      return
    }
    const ok = await onLogin(nextKey)
    if (ok && sharedAccessKey) {
      stripSharedAccessKeyFromCurrentUrl()
      setSharedAccessKey(null)
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-slate-950 px-5 py-8 text-slate-50">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(34,211,238,0.22),_transparent_36%),radial-gradient(circle_at_bottom,_rgba(124,58,237,0.18),_transparent_42%)]" />
      <Card className="relative w-full max-w-md border-white/10 bg-white/10 text-slate-50 shadow-2xl backdrop-blur-2xl">
        <CardHeader className="space-y-4 text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-cyan-400/15 text-cyan-200">
            <KeyRound className="h-7 w-7" />
          </div>
          <div>
            <CardTitle className="text-2xl">微信面签模拟 MVP</CardTitle>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              输入 access key，或启用分享链接中的 Key 后，在手机里完成一轮轻量面签练习。
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            {hasSharedAccessKey ? (
              <div className="rounded-xl border border-cyan-200/15 bg-cyan-200/[0.08] px-3 py-3 text-sm leading-6 text-cyan-50">
                <div className="font-semibold">已识别分享链接中的授权 Key</div>
                <div className="mt-1 font-mono text-xs text-cyan-100/80">
                  {maskedSharedAccessKey}
                </div>
                <p className="mt-2 text-xs text-slate-300">
                  点击下方按钮即可启用；验证成功后会清理地址栏中的 Key。
                </p>
              </div>
            ) : (
              <Input
                value={accessKey}
                onChange={(event) => setAccessKey(event.target.value)}
                placeholder="请输入 access key"
                className="border-white/10 bg-white/10 text-slate-50 placeholder:text-slate-400"
                type="password"
                disabled={isChecking || isLoggingIn}
              />
            )}
            {error ? (
              <p className="rounded-xl border border-red-400/25 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                {error}
              </p>
            ) : null}
            <Button
              type="submit"
              className="h-11 w-full rounded-xl bg-cyan-300 text-slate-950 hover:bg-cyan-200"
              disabled={isChecking || isLoggingIn || !(sharedAccessKey ?? accessKey).trim()}
            >
              {isChecking || isLoggingIn ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              {hasSharedAccessKey ? "启用分享 Key" : "进入模拟面签"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
