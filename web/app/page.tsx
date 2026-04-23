"use client"

import { useState } from "react"
import { cn } from "@/lib/utils"

import { AnalysisPanel } from "@/components/ds160/analysis-panel"
import { ChatPanel } from "@/components/ds160/chat-panel"
import { ReportModal } from "@/components/ds160/report-modal"
import { Sidebar } from "@/components/ds160/sidebar"
import { TopBar } from "@/components/ds160/top-bar"
import { VisaSelector } from "@/components/ds160/visa-selector"
import { useSessionWorkbench } from "@/hooks/use-session-workbench"

export default function DS160Workbench() {
  const [activeNavItem, setActiveNavItem] = useState("workbench")
  const [activeTab, setActiveTab] = useState("simulation")

  const {
    mockMode,
    sessionId,
    visaType,
    isInitializing,
    messages,
    isSending,
    isUploading,
    chatError,
    userReport,
    isLoadingReport,
    reportError,
    internalReport,
    isLoadingInternalReport,
    modalError,
    isReportModalOpen,
    setIsReportModalOpen,
    isPaused,
    sessionTimeLabel,
    initError,
    handleVisaSelect,
    handleSendMessage,
    handleUploadFile,
    handleRequestHint,
    handleContinueAnswer,
    handleViewDetails,
    handleActionClick,
    handlePause,
    handleEndSession,
    handleReset,
  } = useSessionWorkbench()

  if (!sessionId) {
    return (
      <VisaSelector
        onSelect={handleVisaSelect}
        isLoading={isInitializing}
        error={initError}
        mockMode={mockMode}
      />
    )
  }

  return (
    <div className="h-screen flex bg-background overflow-hidden relative">
      <div className="absolute top-4 left-4 flex items-center gap-2 z-50">
        <div className="w-3 h-3 rounded-full bg-[#FF5F57] border border-[#E0443E]" />
        <div className="w-3 h-3 rounded-full bg-[#FEBC2E] border border-[#DEA123]" />
        <div className="w-3 h-3 rounded-full bg-[#28C840] border border-[#1AAB29]" />
      </div>

      {mockMode && (
        <div className="absolute top-4 left-24 z-50 rounded-full border border-amber-300 bg-amber-50/95 px-3 py-1 text-xs font-medium text-amber-800 shadow-sm backdrop-blur">
          开发模式：当前使用 Mock 数据
        </div>
      )}

      <Sidebar activeItem={activeNavItem} onItemClick={setActiveNavItem} />

      <div className="flex-1 flex flex-col min-w-0">
        <TopBar
          visaType={visaType || "F-1"}
          sessionTime={sessionTimeLabel}
          isPaused={isPaused}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          onPause={handlePause}
          onEndSession={handleEndSession}
          onReset={handleReset}
        />

        <div className="flex-1 flex min-h-0">
          <main
            className={cn(
              "flex flex-1 min-h-0 min-w-0 flex-col",
              activeTab === "coach" ? "p-3" : "p-4",
            )}
          >
            <ChatPanel
              messages={messages}
              onSendMessage={handleSendMessage}
              onUploadFile={handleUploadFile}
              onRequestHint={handleRequestHint}
              onContinueAnswer={handleContinueAnswer}
              isSending={isSending}
              isUploading={isUploading}
              error={chatError}
            />
          </main>

          <AnalysisPanel
            report={userReport}
            isLoading={isLoadingReport}
            error={reportError}
            mode={activeTab === "coach" ? "coach" : "simulation"}
            onViewDetails={handleViewDetails}
            onViewAllMaterials={handleViewDetails}
            onActionClick={handleActionClick}
          />
        </div>
      </div>

      <ReportModal
        open={isReportModalOpen}
        onOpenChange={setIsReportModalOpen}
        userReport={userReport}
        internalReport={internalReport}
        isLoading={isLoadingInternalReport}
        error={modalError}
      />
    </div>
  )
}
