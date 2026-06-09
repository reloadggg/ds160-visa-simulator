"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

import { AnalysisPanel } from "@/components/ds160/analysis-panel"
import { ChatPanel } from "@/components/ds160/chat-panel"
import { HistoryPanel } from "@/components/ds160/history-panel"
import { MaterialsPanel } from "@/components/ds160/materials-panel"
import { ReportModal } from "@/components/ds160/report-modal"
import { RuntimeDebugPanel } from "@/components/ds160/runtime-debug-panel"
import { SettingsPanel } from "@/components/ds160/settings-panel"
import { Sidebar, navItems } from "@/components/ds160/sidebar"
import { TopBar } from "@/components/ds160/top-bar"
import { VisaSelector } from "@/components/ds160/visa-selector"
import { WorkbenchThemeToggle } from "@/components/ds160/workbench-theme-toggle"
import { AuthGuard } from "@/components/ds160/auth-guard"
import { useAuth } from "@/hooks/use-auth"
import { useSessionWorkbench } from "@/hooks/use-session-workbench"
import { APP_VERSION_LABEL } from "@/lib/app-version"
import { getAppConfig } from "@/lib/api/client"
import { LogOut } from "lucide-react"
import type {
  AppConfig,
  SessionHistoryEntry,
  UserModelConfig,
} from "@/lib/api/types"

const DEFAULT_APP_CONFIG: AppConfig = {
  show_github_link: false,
  wx_entry_enabled: false,
  debug_console_enabled: false,
  debug_material_enabled: false,
  user_model_config_enabled: false,
  rag_status_user_visible: false,
}

const DISABLED_USER_MODEL_CONFIG: UserModelConfig = {
  enabled: false,
  streamingEnabled: false,
  baseUrl: "",
  apiKey: "",
  model: "",
}

export default function DS160Workbench() {
  return (
    <AuthGuard>
      <Workbench />
    </AuthGuard>
  )
}

function Workbench() {
  const router = useRouter()
  const [activeNavItem, setActiveNavItem] = useState("workbench")
  const [appConfig, setAppConfig] = useState<AppConfig>(DEFAULT_APP_CONFIG)
  const {
    userProfile,
    accessKeyQuota,
    currentAccessKeyShareLink,
    maskedCurrentAccessKey,
    logout,
    updateUserProfile,
  } = useAuth()

  const {
    apiBaseUrl,
    mockMode,
    sessionId,
    isInterviewTerminal,
    visaType,
    isInitializing,
    messages,
    activityEvents,
    isSending,
    isUploading,
    chatError,
    userReport,
    isLoadingReport,
    reportError,
    internalReport,
    interviewReview,
    isGeneratingReview,
    isLoadingInternalReport,
    modalError,
    isReportModalOpen,
    handleReportModalOpenChange,
    handleGenerateInterviewReview,
    isPaused,
    initError,
    uploadedMaterials,
    sessionHistory,
    composerCommand,
    settingsFeedback,
    isDebugBundleGenerating,
    debugBundleProgress,
    materialPackages,
    isLoadingMaterialPackages,
    isImportingMaterialPackage,
    runtimeDebugSnapshot,
    runtimeDebugEvents,
    latestDebugMaterialBundle,
    isLoadingRuntimeDebug,
    runtimeDebugError,
    modelConfigError,
    ragStatus,
    isLoadingRagStatus,
    isUploadingRagFile,
    ragError,
    handleComposerCommandHandled,
    handleVisaSelect,
    handleSendMessage,
    handleRetryMessage,
    handleViewDetails,
    handleActionClick,
    handlePause,
    handleEndSession,
    handleReset,
    handleCopySessionId,
    handleUserModelConfigChange,
    handleUploadRagFile,
    refreshRagStatus,
    handleExportSession,
    handleExportConversationImage,
    handleExportReviewImage,
    refreshRuntimeDebugSnapshot,
    handleCopyRuntimeDebugPackage,
    handleDebugMaterialBundleScenario,
    refreshMaterialPackages,
    handleImportMaterialPackage,
    handleClearCurrentKeyMaterials,
    handleClearHistory,
    handleRestoreSession,
  } = useSessionWorkbench()

  const handleLogoutToHome = async () => {
    await logout()
    router.replace("/")
  }

  useEffect(() => {
    let cancelled = false
    getAppConfig()
      .then((config) => {
        if (!cancelled) {
          setAppConfig(config)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setAppConfig(DEFAULT_APP_CONFIG)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const effectiveActiveNavItem =
    activeNavItem === "debug" && !appConfig.debug_console_enabled
      ? "workbench"
      : activeNavItem

  useEffect(() => {
    if (!appConfig.user_model_config_enabled) {
      handleUserModelConfigChange(DISABLED_USER_MODEL_CONFIG)
    }
  }, [appConfig.user_model_config_enabled, handleUserModelConfigChange])

  const openProfileSettings = () => {
    setActiveNavItem("settings")
  }

  const handleUpdateUserDisplayName = (displayName: string) => {
    updateUserProfile(displayName)
  }

  const handleCopyCurrentKeyShareLink = async () => {
    if (!currentAccessKeyShareLink) {
      return false
    }
    try {
      await navigator.clipboard.writeText(currentAccessKeyShareLink)
      return true
    } catch {
      window.prompt("复制失败，请手动复制当前 Key 分享链接：", currentAccessKeyShareLink)
      return false
    }
  }

  const onRestoreSession = (entry: SessionHistoryEntry) => {
    handleRestoreSession(entry)
    setActiveNavItem("workbench")
  }
  useEffect(() => {
    if (effectiveActiveNavItem !== "debug" || !sessionId) {
      return
    }
    void refreshRuntimeDebugSnapshot(sessionId)
  }, [effectiveActiveNavItem, refreshRuntimeDebugSnapshot, sessionId])

  const renderHeader = () => {
    if (sessionId) {
      return (
        <TopBar
          visaType={visaType || "F-1"}
          isPaused={isPaused}
          userName={userProfile?.displayName ?? "User"}
          userAvatarUrl={userProfile?.avatarUrl ?? "/default-user-avatar.svg"}
          mockMode={mockMode}
          onPause={handlePause}
          onEndSession={handleEndSession}
          onReset={handleReset}
          onDebugMaterialBundleScenario={
            appConfig.debug_material_enabled
              ? handleDebugMaterialBundleScenario
              : undefined
          }
          isDebugBundleGenerating={isDebugBundleGenerating}
          onExportConversationImage={handleExportConversationImage}
          onLogout={() => void handleLogoutToHome()}
          onEditUserName={openProfileSettings}
        />
      )
    }

    return (
      <header className="m-3 flex h-16 shrink-0 items-center justify-between rounded-[28px] border border-white/70 bg-white/60 px-4 shadow-lg shadow-blue-950/5 backdrop-blur-2xl dark:border-white/10 dark:bg-black/35 dark:shadow-black/25 md:mx-4 md:px-6">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold text-foreground md:text-lg">
              模拟面签工作台
            </h2>
            <span className="shrink-0 rounded-xl border border-blue-100 bg-blue-50/70 px-2 py-0.5 font-mono text-[11px] text-blue-700 dark:border-cyan-200/15 dark:bg-cyan-200/[0.06] dark:text-cyan-100/80">
              {APP_VERSION_LABEL}
            </span>
          </div>
          <p className="hidden truncate text-sm text-muted-foreground sm:block">
            先选择签证类型开始新会话，也可以查看本地历史记录和材料归档。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <WorkbenchThemeToggle />
          {mockMode ? (
            <div className="rounded-full border border-amber-300 bg-amber-50/95 px-3 py-1 text-[10px] font-medium text-amber-800 shadow-sm backdrop-blur dark:border-amber-300/25 dark:bg-amber-300/10 dark:text-amber-100 md:text-xs">
              {sessionId ? "MOCK" : "开发模式：Mock 数据"}
            </div>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleLogoutToHome()}
            className="gap-2 rounded-full bg-white/70 dark:border-white/12 dark:bg-white/[0.06] dark:text-slate-100 dark:hover:bg-white/[0.1]"
          >
            <LogOut className="h-4 w-4" />
            <span className="hidden sm:inline">退出当前 Key</span>
            <span className="sm:hidden">退出</span>
          </Button>
        </div>
      </header>
    )
  }

  const renderWorkbench = () => {
    if (!sessionId) {
      return (
        <VisaSelector
          embedded
          onSelect={handleVisaSelect}
          isLoading={isInitializing}
          error={initError}
          mockMode={mockMode}
          accessKeyQuota={accessKeyQuota}
        />
      )
    }

    return (
      <div className="flex h-full min-h-0 min-w-0">
        <main
          className={cn("flex min-h-0 min-w-0 flex-1 flex-col", "p-2 md:p-4")}
        >
          <ChatPanel
            messages={messages}
            activityEvents={activityEvents}
            onSendMessage={handleSendMessage}
            onRetryMessage={handleRetryMessage}
            userName={userProfile?.displayName ?? "User"}
            userAvatarUrl={userProfile?.avatarUrl ?? "/default-user-avatar.svg"}
            isSending={isSending}
            isUploading={isUploading}
            isSessionEnded={isInterviewTerminal}
            error={chatError}
            composerCommand={composerCommand}
            onComposerCommandHandled={handleComposerCommandHandled}
          />
        </main>

        <AnalysisPanel
          className="hidden xl:flex"
          report={userReport}
          isLoading={isLoadingReport}
          error={reportError}
          mode="coach"
          materials={uploadedMaterials}
          onViewDetails={handleViewDetails}
          onViewAllMaterials={() => setActiveNavItem("materials")}
          onActionClick={handleActionClick}
        />
      </div>
    )
  }

  return (
    <>
      <div className="relative flex h-[100dvh] overflow-hidden bg-[radial-gradient(circle_at_15%_10%,rgba(37,99,235,.14),transparent_32%),radial-gradient(circle_at_85%_0%,rgba(14,165,233,.12),transparent_28%),linear-gradient(135deg,#f8fbff,#edf4ff)] dark:bg-[#050608] dark:bg-[radial-gradient(circle_at_18%_10%,rgba(59,130,246,0.20),transparent_32%),radial-gradient(circle_at_82%_16%,rgba(168,85,247,0.16),transparent_28%),linear-gradient(180deg,rgba(255,255,255,0.045),transparent_32%)]">
        <div className="pointer-events-none fixed left-1/2 top-0 hidden h-[420px] w-[720px] -translate-x-1/2 rounded-full bg-cyan-300/8 blur-3xl dark:block" />
        <div className="absolute left-4 top-4 z-50 hidden items-center gap-2 lg:flex">
          <div className="h-3 w-3 rounded-full border border-[#E0443E] bg-[#FF5F57]" />
          <div className="h-3 w-3 rounded-full border border-[#DEA123] bg-[#FEBC2E]" />
          <div className="h-3 w-3 rounded-full border border-[#1AAB29] bg-[#28C840]" />
        </div>

        <Sidebar
          activeItem={effectiveActiveNavItem}
          onItemClick={setActiveNavItem}
          showDebug={appConfig.debug_console_enabled}
          showGithub={appConfig.show_github_link}
        />

        <div className="flex min-w-0 flex-1 flex-col">
          {renderHeader()}
          <div className="min-h-0 flex-1 overflow-y-auto pb-[calc(4.75rem+env(safe-area-inset-bottom))] lg:pb-0">
            <div
              className={cn(
                "h-full",
                effectiveActiveNavItem === "workbench" ? "block" : "hidden",
              )}
            >
              {renderWorkbench()}
            </div>
            <div
              className={cn(
                "h-full",
                effectiveActiveNavItem === "history" ? "block" : "hidden",
              )}
            >
              <HistoryPanel
                entries={sessionHistory}
                onRestore={onRestoreSession}
              />
            </div>
            <div
              className={cn(
                "h-full",
                effectiveActiveNavItem === "materials" ? "block" : "hidden",
              )}
            >
              <MaterialsPanel
                currentMaterials={uploadedMaterials}
                historyEntries={sessionHistory}
                currentSessionId={sessionId}
              />
            </div>
            <div
              className={cn(
                "h-full",
                effectiveActiveNavItem === "debug" &&
                  appConfig.debug_console_enabled
                  ? "block"
                  : "hidden",
              )}
            >
              <RuntimeDebugPanel
                sessionId={sessionId}
                mockMode={mockMode}
                apiBaseUrl={apiBaseUrl}
                snapshot={runtimeDebugSnapshot}
                liveEvents={runtimeDebugEvents}
                latestDebugBundle={latestDebugMaterialBundle}
                isLoading={isLoadingRuntimeDebug}
                error={runtimeDebugError}
                onRefresh={() => void refreshRuntimeDebugSnapshot(sessionId)}
                onCopyDebugPackage={handleCopyRuntimeDebugPackage}
              />
            </div>
            <div
              className={cn(
                "h-full",
                effectiveActiveNavItem === "settings" ? "block" : "hidden",
              )}
            >
              <SettingsPanel
                sessionId={sessionId}
                visaType={visaType}
                historyCount={sessionHistory.length}
                feedback={settingsFeedback}
                isDebugBundleGenerating={isDebugBundleGenerating}
                debugBundleProgress={debugBundleProgress}
                materialPackages={materialPackages}
                isLoadingMaterialPackages={isLoadingMaterialPackages}
                isImportingMaterialPackage={isImportingMaterialPackage}
                modelConfigError={modelConfigError}
                ragStatus={ragStatus}
                isLoadingRagStatus={isLoadingRagStatus}
                isUploadingRagFile={isUploadingRagFile}
                ragError={ragError}
                onUploadRagFile={handleUploadRagFile}
                onRefreshRagStatus={refreshRagStatus}
                onCopySessionId={handleCopySessionId}
                onExportSession={handleExportSession}
                onExportConversationImage={handleExportConversationImage}
                onDebugMaterialBundleScenario={
                  handleDebugMaterialBundleScenario
                }
                onRefreshMaterialPackages={refreshMaterialPackages}
                onImportMaterialPackage={handleImportMaterialPackage}
                onResetCurrentSession={handleReset}
                onClearHistory={handleClearHistory}
                onClearCurrentKeyMaterials={handleClearCurrentKeyMaterials}
                onCopyCurrentKeyShareLink={handleCopyCurrentKeyShareLink}
                onLogout={() => void handleLogoutToHome()}
                accessKeyQuota={accessKeyQuota}
                currentAccessKeyPreview={maskedCurrentAccessKey}
                showGithub={appConfig.show_github_link}
                showUserModelConfig={appConfig.user_model_config_enabled}
                showRagStatus={appConfig.rag_status_user_visible}
                showDebugTools={appConfig.debug_material_enabled}
                userDisplayName={userProfile?.displayName ?? ""}
                onUpdateUserDisplayName={handleUpdateUserDisplayName}
              />
            </div>
          </div>
        </div>

        {/* Mobile Bottom Navigation */}
        <nav className="fixed bottom-0 left-0 right-0 z-50 flex h-[calc(4rem+env(safe-area-inset-bottom))] items-center border-t border-border bg-card pb-[env(safe-area-inset-bottom)] lg:hidden">
          <ul className="flex w-full justify-around px-2">
            {navItems
              .filter(
                (item) =>
                  item.id !== "debug" || appConfig.debug_console_enabled,
              )
              .map((item) => {
                const Icon = item.icon
                const isActive = effectiveActiveNavItem === item.id
                return (
                  <li key={item.id} className="flex-1">
                    <button
                      onClick={() => setActiveNavItem(item.id)}
                      className={cn(
                        "flex w-full flex-col items-center justify-center gap-1 py-2 transition-colors",
                        isActive
                          ? "text-primary"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <Icon className="h-5 w-5" />
                      <span className="text-[10px] font-medium">
                        {item.label}
                      </span>
                    </button>
                  </li>
                )
              })}
          </ul>
        </nav>
      </div>

      <ReportModal
        open={isReportModalOpen}
        onOpenChange={handleReportModalOpenChange}
        userReport={userReport}
        internalReport={internalReport}
        interviewReview={interviewReview}
        isLoading={isLoadingInternalReport}
        isGeneratingReview={isGeneratingReview}
        error={modalError}
        onGenerateReview={handleGenerateInterviewReview}
        onExportReviewImage={handleExportReviewImage}
      />
    </>
  )
}
