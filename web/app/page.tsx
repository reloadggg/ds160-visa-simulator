"use client"

import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

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
import { AuthGuard } from "@/components/ds160/auth-guard"
import { useAuth } from "@/hooks/use-auth"
import { useSessionWorkbench } from "@/hooks/use-session-workbench"
import { APP_VERSION_LABEL } from "@/lib/app-version"
import type { SessionHistoryEntry } from "@/lib/api/types"

export default function DS160Workbench() {
  return (
    <AuthGuard>
      <Workbench />
    </AuthGuard>
  )
}

function Workbench() {
  const [activeNavItem, setActiveNavItem] = useState("workbench")
  const [activeTab, setActiveTab] = useState("simulation")
  const { userProfile } = useAuth()

  const {
    apiBaseUrl,
    mockMode,
    sessionId,
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
    sessionTimeLabel,
    initError,
    uploadedMaterials,
    sessionHistory,
    composerCommand,
    settingsFeedback,
    isDebugBundleGenerating,
    debugBundleProgress,
    runtimeDebugSnapshot,
    runtimeDebugEvents,
    latestDebugMaterialBundle,
    isLoadingRuntimeDebug,
    runtimeDebugError,
    userModelConfig,
    availableModels,
    isLoadingModels,
    modelConfigError,
    ragStatus,
    isLoadingRagStatus,
    isUploadingRagFile,
    ragError,
    handleComposerCommandHandled,
    handleVisaSelect,
    handleSendMessage,
    handleViewDetails,
    handleActionClick,
    handlePause,
    handleEndSession,
    handleReset,
    handleCopySessionId,
    handleUserModelConfigChange,
    handleFetchUserModels,
    handleUploadRagFile,
    refreshRagStatus,
    handleExportSession,
    handleExportConversationImage,
    handleExportReviewImage,
    refreshRuntimeDebugSnapshot,
    handleCopyRuntimeDebugPackage,
    handleDebugMaterialBundleScenario,
    handleClearHistory,
    handleRestoreSession,
  } = useSessionWorkbench()

  const onRestoreSession = (entry: SessionHistoryEntry) => {
    handleRestoreSession(entry)
    setActiveNavItem("workbench")
  }

  useEffect(() => {
    if (activeNavItem !== "debug" || !sessionId) {
      return
    }
    void refreshRuntimeDebugSnapshot(sessionId)
  }, [activeNavItem, refreshRuntimeDebugSnapshot, sessionId])

  const renderHeader = () => {
    if (sessionId) {
      return (
        <TopBar
          visaType={visaType || "F-1"}
          sessionTime={sessionTimeLabel}
          isPaused={isPaused}
          activeTab={activeTab}
          userName={userProfile?.displayName ?? "User"}
          userAvatarUrl={userProfile?.avatarUrl ?? "/default-user-avatar.svg"}
          mockMode={mockMode}
          onTabChange={setActiveTab}
          onPause={handlePause}
          onEndSession={handleEndSession}
          onReset={handleReset}
          onDebugMaterialBundleScenario={handleDebugMaterialBundleScenario}
          isDebugBundleGenerating={isDebugBundleGenerating}
          onExportConversationImage={handleExportConversationImage}
        />
      )
    }

    return (
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-card px-4 md:px-6">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-semibold text-foreground md:text-lg">DS-160 面签工作台</h2>
            <span className="shrink-0 rounded-md border border-border bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
              {APP_VERSION_LABEL}
            </span>
          </div>
          <p className="hidden truncate text-sm text-muted-foreground sm:block">
            先选择签证类型开始新会话，也可以查看本地历史记录和材料归档。
          </p>
        </div>
        {mockMode ? (
          <div className="rounded-full border border-amber-300 bg-amber-50/95 px-3 py-1 text-[10px] font-medium text-amber-800 shadow-sm backdrop-blur md:text-xs">
            {sessionId ? "MOCK" : "开发模式：Mock 数据"}
          </div>
        ) : null}
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
        />
      )
    }

    return (
      <div className="flex h-full min-h-0 min-w-0">
        <main
          className={cn(
            "flex min-h-0 min-w-0 flex-1 flex-col",
            activeTab === "coach" ? "p-2 md:p-3" : "p-2 md:p-4",
          )}
        >
          <ChatPanel
            messages={messages}
            activityEvents={activityEvents}
            onSendMessage={handleSendMessage}
            userName={userProfile?.displayName ?? "User"}
            userAvatarUrl={userProfile?.avatarUrl ?? "/default-user-avatar.svg"}
            isSending={isSending}
            isUploading={isUploading}
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
          mode={activeTab === "coach" ? "coach" : "simulation"}
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
      <div className="relative flex h-[100dvh] overflow-hidden bg-background">
        <div className="absolute left-4 top-4 z-50 hidden items-center gap-2 lg:flex">
          <div className="h-3 w-3 rounded-full border border-[#E0443E] bg-[#FF5F57]" />
          <div className="h-3 w-3 rounded-full border border-[#DEA123] bg-[#FEBC2E]" />
          <div className="h-3 w-3 rounded-full border border-[#1AAB29] bg-[#28C840]" />
        </div>

        <Sidebar activeItem={activeNavItem} onItemClick={setActiveNavItem} />

        <div className="flex min-w-0 flex-1 flex-col">
          {renderHeader()}
          <div className="min-h-0 flex-1 overflow-y-auto pb-[calc(4.75rem+env(safe-area-inset-bottom))] lg:pb-0">
            <div className={cn("h-full", activeNavItem === "workbench" ? "block" : "hidden")}>
              {renderWorkbench()}
            </div>
            <div className={cn("h-full", activeNavItem === "history" ? "block" : "hidden")}>
              <HistoryPanel entries={sessionHistory} onRestore={onRestoreSession} />
            </div>
            <div className={cn("h-full", activeNavItem === "materials" ? "block" : "hidden")}>
              <MaterialsPanel
                currentMaterials={uploadedMaterials}
                historyEntries={sessionHistory}
                currentSessionId={sessionId}
              />
            </div>
            <div className={cn("h-full", activeNavItem === "debug" ? "block" : "hidden")}>
              <RuntimeDebugPanel
                sessionId={sessionId}
                snapshot={runtimeDebugSnapshot}
                liveEvents={runtimeDebugEvents}
                latestDebugBundle={latestDebugMaterialBundle}
                isLoading={isLoadingRuntimeDebug}
                error={runtimeDebugError}
                onRefresh={() => void refreshRuntimeDebugSnapshot(sessionId)}
                onCopyDebugPackage={handleCopyRuntimeDebugPackage}
              />
            </div>
            <div className={cn("h-full", activeNavItem === "settings" ? "block" : "hidden")}>
              <SettingsPanel
                mockMode={mockMode}
                apiBaseUrl={apiBaseUrl}
                sessionId={sessionId}
                historyCount={sessionHistory.length}
                feedback={settingsFeedback}
                isDebugBundleGenerating={isDebugBundleGenerating}
                debugBundleProgress={debugBundleProgress}
                userModelConfig={userModelConfig}
                availableModels={availableModels}
                isLoadingModels={isLoadingModels}
                modelConfigError={modelConfigError}
                ragStatus={ragStatus}
                isLoadingRagStatus={isLoadingRagStatus}
                isUploadingRagFile={isUploadingRagFile}
                ragError={ragError}
                onUserModelConfigChange={handleUserModelConfigChange}
                onFetchUserModels={handleFetchUserModels}
                onUploadRagFile={handleUploadRagFile}
                onRefreshRagStatus={refreshRagStatus}
                onCopySessionId={handleCopySessionId}
                onExportSession={handleExportSession}
                onExportConversationImage={handleExportConversationImage}
                onDebugMaterialBundleScenario={handleDebugMaterialBundleScenario}
                onResetCurrentSession={handleReset}
                onClearHistory={handleClearHistory}
              />
            </div>
          </div>
        </div>

        {/* Mobile Bottom Navigation */}
        <nav className="fixed bottom-0 left-0 right-0 z-50 flex h-[calc(4rem+env(safe-area-inset-bottom))] items-center border-t border-border bg-card pb-[env(safe-area-inset-bottom)] lg:hidden">
          <ul className="flex w-full justify-around px-2">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = activeNavItem === item.id
              return (
                <li key={item.id} className="flex-1">
                  <button
                    onClick={() => setActiveNavItem(item.id)}
                    className={cn(
                      "flex w-full flex-col items-center justify-center gap-1 py-2 transition-colors",
                      isActive
                        ? "text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <Icon className="h-5 w-5" />
                    <span className="text-[10px] font-medium">{item.label}</span>
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
