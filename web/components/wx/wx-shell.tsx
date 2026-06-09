"use client"

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

export function WxShell() {
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
      <div className="fixed inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.20),_transparent_34%),radial-gradient(circle_at_bottom_right,_rgba(124,58,237,0.18),_transparent_40%)]" />
      <div className="relative mx-auto min-h-dvh w-full max-w-md pb-8">
        <header className="sticky top-0 z-20 border-b border-white/10 bg-slate-950/75 px-4 pb-3 pt-[calc(0.75rem+env(safe-area-inset-top))] backdrop-blur-2xl">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm text-cyan-100">
                {workbench.visaType ? `${workbench.visaType} 微信面签` : "微信面签模拟"}
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
              error={workbench.chatError}
              onSend={workbench.sendTextMessage}
            />
            <WxUploadEntry
              disabled={!workbench.sessionId}
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
