"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import {
  ApiError,
  createSession,
  getInternalReport,
  getRequiredPackage,
  getUserReport,
  sendMessage,
  uploadFile,
} from "@/lib/api/client"
import {
  getMockRequiredPackage,
  isMockMode,
  MOCK_INTERNAL_REPORT,
  MOCK_MESSAGES,
  MOCK_SESSION_ID,
  MOCK_USER_REPORT,
} from "@/lib/api/mock-data"
import { toDocumentLabel } from "@/lib/api/mappers"
import type {
  AllowedAction,
  ChatMessage,
  InternalReport,
  RequiredPackage,
  Session,
  UserReport,
  VisaFamily,
} from "@/lib/api/types"

function getTimestamp(): string {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatRequestedDocuments(labels: string[]): string {
  if (!labels.length) {
    return ""
  }

  return labels.join("、")
}

function buildRequiredPackageMessage(visaFamily: VisaFamily, requiredPackage: RequiredPackage): string {
  return `欢迎开始 ${visaFamily} 签证面签模拟。请准备以下材料：${formatRequestedDocuments(
    requiredPackage.required_initial_package_labels,
  )}`
}

function buildRequestedDocumentsMessage(
  requestedDocumentLabels: string[],
  governorDecision?: string | null,
): string | null {
  if (!requestedDocumentLabels.length) {
    return null
  }

  const documentList = formatRequestedDocuments(requestedDocumentLabels)
  if (governorDecision === "need_more_evidence") {
    return `系统建议优先补充以下材料：${documentList}。你可以上传材料，或先继续解释相关细节。`
  }

  return `后端当前仍关注这些材料：${documentList}。`
}

function buildGateProgressMessage(overallStatus?: string): string | null {
  if (overallStatus === "waiting_for_parse") {
    return "材料已收到，系统正在解析，请稍后继续查看更新。"
  }

  return null
}

export function useSessionWorkbench() {
  const mockMode = useMemo(() => isMockMode(), [])

  const [session, setSession] = useState<Session | null>(null)
  const [visaType, setVisaType] = useState<VisaFamily | null>(null)
  const [requiredPackage, setRequiredPackage] = useState<RequiredPackage | null>(null)

  const [isInitializing, setIsInitializing] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)

  const [userReport, setUserReport] = useState<UserReport | null>(null)
  const [isLoadingReport, setIsLoadingReport] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)

  const [internalReport, setInternalReport] = useState<InternalReport | null>(null)
  const [isLoadingInternalReport, setIsLoadingInternalReport] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [isReportModalOpen, setIsReportModalOpen] = useState(false)

  const [isPaused, setIsPaused] = useState(false)
  const [sessionTime, setSessionTime] = useState(0)

  const sessionId = session?.session_id ?? null

  useEffect(() => {
    if (!sessionId || isPaused) {
      return
    }

    const timer = window.setInterval(() => {
      setSessionTime((prev) => prev + 1)
    }, 1000)

    return () => {
      window.clearInterval(timer)
    }
  }, [isPaused, sessionId])

  const appendMessage = useCallback((message: Omit<ChatMessage, "id" | "timestamp">) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `${message.role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        timestamp: getTimestamp(),
        ...message,
      },
    ])
  }, [])

  const fetchUserReport = useCallback(
    async (targetSessionId: string): Promise<UserReport | null> => {
      if (mockMode) {
        setUserReport(MOCK_USER_REPORT)
        return MOCK_USER_REPORT
      }

      setIsLoadingReport(true)
      setReportError(null)
      try {
        const report = await getUserReport(targetSessionId)
        setUserReport(report)
        return report
      } catch (error) {
        const message =
          error instanceof ApiError
            ? `获取报告失败：${error.message}`
            : "获取报告失败，请稍后重试。"
        setReportError(message)
        return null
      } finally {
        setIsLoadingReport(false)
      }
    },
    [mockMode],
  )

  const refreshReports = useCallback(
    async (targetSessionId: string): Promise<void> => {
      await fetchUserReport(targetSessionId)
    },
    [fetchUserReport],
  )

  const handleVisaSelect = useCallback(
    async (visaFamily: VisaFamily) => {
      setIsInitializing(true)
      setInitError(null)
      setChatError(null)
      setReportError(null)
      setModalError(null)

      try {
        if (mockMode) {
          const mockRequiredPackage = getMockRequiredPackage(visaFamily)
          setSession({
            session_id: MOCK_SESSION_ID,
            phase_state: "interview",
            current_governor_decision: MOCK_USER_REPORT.governor_decision ?? null,
            gate_status: null,
          })
          setVisaType(visaFamily)
          setRequiredPackage(mockRequiredPackage)
          setMessages([
            {
              id: "system-1",
              role: "system",
              content: buildRequiredPackageMessage(visaFamily, mockRequiredPackage),
              timestamp: getTimestamp(),
            },
            ...MOCK_MESSAGES.slice(1),
          ])
          setUserReport(MOCK_USER_REPORT)
          setInternalReport(MOCK_INTERNAL_REPORT)
          setSessionTime(1458)
          return
        }

        const createdSession = await createSession(visaFamily)
        const nextRequiredPackage = await getRequiredPackage(createdSession.session_id)

        setSession(createdSession)
        setVisaType(visaFamily)
        setRequiredPackage(nextRequiredPackage)
        setMessages([
          {
            id: "system-1",
            role: "system",
            content: buildRequiredPackageMessage(visaFamily, nextRequiredPackage),
            timestamp: getTimestamp(),
          },
        ])
        setSessionTime(0)

        await fetchUserReport(createdSession.session_id)
      } catch (error) {
        const message =
          error instanceof ApiError
            ? `初始化失败：${error.message}`
            : "无法连接到服务器，请确认后端已启动。"
        setInitError(message)
      } finally {
        setIsInitializing(false)
      }
    },
    [fetchUserReport, mockMode],
  )

  const handleSendMessage = useCallback(
    async (content: string) => {
      if (!sessionId || isSending) {
        return
      }

      appendMessage({
        role: "user",
        content,
      })

      setIsSending(true)
      setChatError(null)

      try {
        if (mockMode) {
          const mockResponses = [
            "好的，我明白了。请告诉我更多关于你的资金来源。谁来支付你的学费和生活费？",
            "你有没有在美国的亲戚或朋友？他们是什么签证身份？",
            "你之前有没有去过其他国家？可以简单介绍一下你的旅行经历吗？",
            "你的父母是做什么工作的？他们对你出国留学有什么看法？",
          ]
          const randomResponse =
            mockResponses[Math.floor(Math.random() * mockResponses.length)]
          appendMessage({
            role: "officer",
            content: randomResponse,
          })
          return
        }

        const response = await sendMessage(sessionId, content)
        appendMessage({
          role: "officer",
          content: response.assistant_message,
        })

        const requestedDocumentsMessage = buildRequestedDocumentsMessage(
          response.requested_document_labels,
          response.governor_decision,
        )
        if (requestedDocumentsMessage) {
          appendMessage({
            role: "system",
            content: requestedDocumentsMessage,
          })
        }

        const gateProgressMessage = buildGateProgressMessage(
          response.gate_progress?.overall_status,
        )
        if (gateProgressMessage) {
          appendMessage({
            role: "system",
            content: gateProgressMessage,
          })
        }

        await refreshReports(sessionId)
      } catch (error) {
        let message = "发送失败，请重试。"
        if (error instanceof ApiError) {
          if (error.status === 401) {
            message = "大模型认证失败，请检查后端 API Key 配置（401）。"
          } else if (error.status === 429) {
            message = "大模型请求频率超限或额度耗尽，请稍后重试（429）。"
          } else if (error.status === 503 || error.status === 502 || error.status === 504) {
            message = "大模型服务当前不可用，请稍后重试（服务异常）。"
          } else {
            message = `发送失败：${error.message}`
          }
        }
        setChatError(message)
      } finally {
        setIsSending(false)
      }
    },
    [appendMessage, isSending, mockMode, refreshReports, sessionId],
  )

  const handleUploadFile = useCallback(
    async (file: File) => {
      if (!sessionId || isUploading) {
        return
      }

      setIsUploading(true)
      setChatError(null)

      try {
        if (mockMode) {
          appendMessage({
            role: "system",
            content: `已上传文件：${file.name}。系统正在分析该材料。`,
          })
          return
        }

        const response = await uploadFile(sessionId, file)
        const uploadFeedback =
          response.feedback_message ??
          response.main_flow_feedback?.message ??
          response.document_assessment?.main_flow_feedback?.message

        appendMessage({
          role: "system",
          content: uploadFeedback || `已上传文件：${file.name}。`,
        })

        const gateProgressMessage = buildGateProgressMessage(
          response.gate_progress?.overall_status,
        )
        if (gateProgressMessage) {
          appendMessage({
            role: "system",
            content: gateProgressMessage,
          })
        }

        const requestedDocumentsMessage = buildRequestedDocumentsMessage(
          response.requested_document_labels,
          null,
        )
        if (requestedDocumentsMessage && !uploadFeedback?.includes(requestedDocumentsMessage)) {
          appendMessage({
            role: "system",
            content: requestedDocumentsMessage,
          })
        }

        await refreshReports(sessionId)
      } catch (error) {
        const message =
          error instanceof ApiError ? `上传失败：${error.message}` : "上传失败，请重试。"
        setChatError(message)
      } finally {
        setIsUploading(false)
      }
    },
    [appendMessage, isUploading, mockMode, refreshReports, sessionId],
  )

  const handleRequestHint = useCallback(() => {
    const firstSuggestion = userReport?.recommended_improvements[0]
    const currentKeyProofLabel =
      userReport?.current_key_proof_label ??
      (userReport?.current_key_proof ? toDocumentLabel(userReport.current_key_proof) : null)

    appendMessage({
      role: "system",
      content:
        firstSuggestion ??
        (currentKeyProofLabel
          ? `优先围绕“${currentKeyProofLabel}”补充说明，给出更具体的事实和证据来源。`
          : "提示：回答问题时尽量具体、直接，并给出能支撑陈述的事实细节。"),
    })
  }, [appendMessage, userReport])

  const handleContinueAnswer = useCallback(() => {
    const currentKeyQuestion = userReport?.current_key_question
    appendMessage({
      role: "system",
      content:
        currentKeyQuestion && currentKeyQuestion !== "暂无"
          ? `请继续围绕“${currentKeyQuestion}”补充回答，优先说明具体事实。`
          : "请继续补充你的回答，可以提供更多细节或背景信息。",
    })
  }, [appendMessage, userReport])

  const handleViewDetails = useCallback(async () => {
    setIsReportModalOpen(true)
    setModalError(null)

    if (!sessionId) {
      return
    }

    if (mockMode) {
      setUserReport(MOCK_USER_REPORT)
      setInternalReport(MOCK_INTERNAL_REPORT)
      return
    }

    setIsLoadingInternalReport(true)
    try {
      const [latestUserReport, latestInternalReport] = await Promise.all([
        getUserReport(sessionId),
        getInternalReport(sessionId),
      ])
      setUserReport(latestUserReport)
      setInternalReport(latestInternalReport)
    } catch (error) {
      const message =
        error instanceof ApiError ? `获取报告失败：${error.message}` : "获取报告失败。"
      setModalError(message)
    } finally {
      setIsLoadingInternalReport(false)
    }
  }, [mockMode, sessionId])

  const handleActionClick = useCallback(
    async (action: AllowedAction) => {
      if (action.intent === "upload") {
        document.querySelector<HTMLInputElement>('input[type="file"]')?.click()
        return
      }

      if (action.intent === "continue") {
        handleContinueAnswer()
        return
      }

      await handleViewDetails()
    },
    [handleContinueAnswer, handleViewDetails],
  )

  const handlePause = useCallback(() => {
    setIsPaused((prev) => !prev)
  }, [])

  const handleEndSession = useCallback(async () => {
    await handleViewDetails()
  }, [handleViewDetails])

  const handleReset = useCallback(() => {
    setSession(null)
    setVisaType(null)
    setRequiredPackage(null)
    setMessages([])
    setUserReport(null)
    setInternalReport(null)
    setInitError(null)
    setChatError(null)
    setReportError(null)
    setModalError(null)
    setIsPaused(false)
    setSessionTime(0)
    setIsReportModalOpen(false)
  }, [])

  const sessionTimeLabel = useMemo(() => {
    const hours = Math.floor(sessionTime / 3600)
    const minutes = Math.floor((sessionTime % 3600) / 60)
    const seconds = sessionTime % 60
    return [hours, minutes, seconds]
      .map((value) => value.toString().padStart(2, "0"))
      .join(":")
  }, [sessionTime])

  return {
    mockMode,
    session,
    sessionId,
    visaType,
    requiredPackage,
    isInitializing,
    initError,
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
  }
}
