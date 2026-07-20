"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { LogOut, PlusCircle, RefreshCcw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { WxAuthScreen } from "@/components/wx/wx-auth-screen"
import { WxChatPanel } from "@/components/wx/wx-chat-panel"
import { WxMaterialStrip } from "@/components/wx/wx-material-strip"
import { WxReportSummary } from "@/components/wx/wx-report-summary"
import { WxUploadEntry } from "@/components/wx/wx-upload-entry"
import { WxVisaPicker } from "@/components/wx/wx-visa-picker"
import { useWxWorkbench } from "@/hooks/use-wx-workbench"
import { getAppConfig } from "@/lib/api/client"

function WxEntryClosedNotice({ detail }: { detail?: string | null }) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-slate-950 px-5 py-8 text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(34,211,238,0.16),_transparent_36%),radial-gradient(circle_at_bottom,_rgba(125,211,252,0.08),_transparent_42%)]" />
      <section className="relative w-full max-w-md rounded-[2rem] border border-white/10 bg-white/[0.065] p-6 text-center shadow-2xl shadow-black/40 backdrop-blur-2xl">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-cyan-200/15 bg-cyan-200/[0.08] font-mono text-sm font-semibold tracking-[0.18em] text-cyan-100">
          WX
        </div>
        <h1 className="mt-5 text-2xl font-semibold">微信端内测中</h1>
        <p className="mt-3 text-sm leading-6 text-slate-300">
          当前入口暂未开放，请先使用桌面工作台完成模拟面签。正式启用后，可从微信内直接进入轻量移动流程。
        </p>
        {detail ? (
          <p className="mt-3 rounded-2xl border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
            {detail}
          </p>
        ) : null}
        <Button
          asChild
          className="mt-6 h-11 w-full rounded-2xl bg-cyan-300 text-slate-950 hover:bg-cyan-200"
        >
          <Link href="/">返回首页</Link>
        </Button>
      </section>
    </main>
  )
}

export function WxShell() {
  const [wxEntryEnabled, setWxEntryEnabled] = useState(false)
  const [configError, setConfigError] = useState<string | null>(null)
  const [isConfigLoading, setIsConfigLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    getAppConfig()
      .then((config) => {
        if (!cancelled) {
          setWxEntryEnabled(config.wx_entry_enabled)
          setConfigError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setWxEntryEnabled(false)
          setConfigError(err instanceof Error ? err.message : "无法读取微信入口配置")
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsConfigLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (isConfigLoading) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-slate-950 text-white">
        <div className="flex items-center gap-3 rounded-3xl border border-white/10 bg-white/[0.06] px-5 py-4">
          <Spinner className="h-5 w-5" />
          <span className="text-sm text-slate-200">正在读取微信入口配置...</span>
        </div>
      </main>
    )
  }

  if (!wxEntryEnabled) {
    return <WxEntryClosedNotice detail={configError} />
  }

  return <WxWorkbenchShell />
}

function WxWorkbenchShell() {
  const workbench = useWxWorkbench()
  const { auth } = workbench

  if (auth.isCheckingAuth || workbench.isInitializing) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-slate-950 text-white">
        <div className="flex items-center gap-3 rounded-3xl border border-white/10 bg-white/[0.06] px-5 py-4">
          <Spinner className="h-5 w-5" />
          <span className="text-sm text-slate-200">正在恢复微信入口...</span>
        </div>
      </main>
    )
  }

  if (!auth.isAuthenticated) {
    return (
      <WxAuthScreen
        isChecking={auth.isCheckingAuth}
        isLoggingIn={auth.isLoggingIn}
        error={auth.error}
        onLogin={auth.login}
      />
    )
  }

  return (
    <main className="min-h-dvh bg-slate-950 text-white">
      <div className="fixed inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.20),_transparent_34%),radial-gradient(circle_at_bottom_right,_rgba(125,211,252,0.08),_transparent_40%)]" />
      <div className="relative mx-auto min-h-dvh w-full max-w-md pb-8">
        <header className="sticky top-0 z-20 border-b border-white/10 bg-slate-950/75 px-4 pb-3 pt-[calc(0.75rem+env(safe-area-inset-top))] backdrop-blur-2xl">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm text-cyan-100">
                {workbench.visaType ? `${workbench.visaType} 微信端面签` : "微信端模拟面签"}
              </div>
              <div className="mt-1 truncate text-xs text-slate-400">
                {workbench.sessionId ?? workbench.quotaLabel ?? "请选择签证类型"}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {workbench.sessionId ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="text-slate-200 hover:bg-white/10"
                  onClick={workbench.resetToVisaPicker}
                  aria-label="新建会话"
                >
                  <PlusCircle className="h-4 w-4" />
                </Button>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="text-slate-200 hover:bg-white/10"
                onClick={() => void auth.logout()}
                aria-label="退出登录"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </header>

        {!workbench.sessionId ? (
          <WxVisaPicker
            sessions={workbench.availableSessions}
            isCreating={workbench.isCreatingSession}
            onStart={workbench.startSession}
            onRestore={workbench.restoreSession}
          />
        ) : (
          <div className="space-y-4 px-3 py-4">
            <WxChatPanel
              messages={workbench.messages}
              isSending={workbench.isSending}
              isSessionTerminal={workbench.isSessionTerminal}
              error={workbench.chatError}
              onSend={workbench.sendTextMessage}
              onRetryMessage={workbench.retryMessage}
            />
            <WxUploadEntry
              disabled={!workbench.sessionId || workbench.isSessionTerminal}
              isUploading={workbench.isUploading}
              isNativeUploadStarting={workbench.isNativeUploadStarting}
              isRefreshingUploadTicket={workbench.isRefreshingUploadTicket}
              uploadError={workbench.uploadError}
              nativeUploadNotice={workbench.nativeUploadNotice}
              onH5Upload={workbench.uploadH5Files}
              onNativeUpload={workbench.startNativeWechatUpload}
            />
            <WxMaterialStrip materials={workbench.uploadedMaterials} />
            <WxReportSummary report={workbench.userReport} error={workbench.reportError} />
            <Button
              type="button"
              variant="outline"
              className="w-full border-white/10 bg-white/5 text-white hover:bg-white/10"
              onClick={() => void workbench.refreshReport()}
            >
              <RefreshCcw className="h-4 w-4" />
              刷新报告摘要
            </Button>
          </div>
        )}
      </div>
    </main>
  )
}
