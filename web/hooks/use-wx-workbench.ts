"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "@/hooks/use-auth"
import {
  createSession,
  createWxUploadTicket,
  fetchSessionMessages,
  getFileContentUrl,
  getUserReport,
  getWxUploadTicketStatus,
  listSessions,
  sendMessage,
  uploadFile,
} from "@/lib/api/client"
import type {
  BackendSessionListItem,
  BackendSessionMessage,
  ChatMessage,
  FileUploadResponse,
  UploadedMaterial,
  UserReport,
  VisaFamily,
  WxUploadTicketStatusResponse,
  WxUploadTicketUploadResult,
} from "@/lib/api/types"
import {
  navigateToNativeUploadPage,
  resolveWxApiBaseUrl,
} from "@/lib/wx/miniprogram-bridge"
import {
  clearPendingWxUploadTicket,
  readPendingWxUploadTicket,
  storePendingWxUploadTicket,
} from "@/lib/wx/upload-return"

export const WX_VISA_FAMILIES: VisaFamily[] = ["F-1", "J-1", "B-1/B-2", "H-1B"]

function isoNow(): string {
  return new Date().toISOString()
}

function visaFamilyFromBackendFamily(value?: string | null): VisaFamily {
  switch ((value ?? "").toLowerCase()) {
    case "f1":
    case "f-1":
      return "F-1"
    case "j1":
    case "j-1":
      return "J-1"
    case "b1_b2":
    case "b-1/b-2":
      return "B-1/B-2"
    case "h1b":
    case "h-1b":
      return "H-1B"
    default:
      return "F-1"
  }
}

function backendMessageToChatMessage(
  message: BackendSessionMessage,
): ChatMessage | null {
  if (message.role !== "assistant" && message.role !== "user") {
    return null
  }
  return {
    id: message.turn_id || `${message.role}-${message.turn_index}`,
    role: message.role,
    content: message.content,
    timestamp: isoNow(),
    status: "sent",
    client_message_id: message.client_message_id ?? null,
  }
}

function fileKind(file: File): UploadedMaterial["kind"] {
  if (file.type.startsWith("image/")) {
    return "image"
  }
  if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
    return "pdf"
  }
  return "file"
}

function uploadResponseToMaterial(
  sessionId: string,
  upload: FileUploadResponse,
  source: {
    name: string
    mimeType?: string | null
    size?: number | null
    uploadedAt?: string | null
    previewUrl?: string | null
  },
): UploadedMaterial {
  const documentId = upload.document_id ?? `upload-${Date.now()}`
  const mimeType = source.mimeType ?? "application/octet-stream"
  const kind: UploadedMaterial["kind"] = mimeType.startsWith("image/")
    ? "image"
    : source.name.toLowerCase().endsWith(".pdf") || mimeType === "application/pdf"
      ? "pdf"
      : "file"

  return {
    id: documentId,
    session_id: sessionId,
    name: source.name,
    mime_type: mimeType,
    kind,
    size: source.size ?? undefined,
    preview_url: source.previewUrl ?? null,
    content_url: upload.content_url ?? (upload.document_id ? getFileContentUrl(sessionId, upload.document_id) : null),
    uploaded_at: source.uploadedAt ?? isoNow(),
    status_label: upload.feedback_message ?? "已上传，材料理解处理中",
    document_id: upload.document_id,
    document_status: upload.document_status,
    understanding_status: upload.understanding_status ?? null,
    understanding_error: upload.understanding_error ?? null,
    document_type: upload.document_type ?? null,
    document_type_label: upload.document_type_label ?? null,
    relevance: upload.relevance ?? null,
    feedback_message: upload.feedback_message ?? null,
    evidence_cards: upload.evidence_cards,
    claims: upload.case_board_delta?.claims,
    proof_points: upload.case_board_delta?.open_proof_points,
    conflicts: upload.case_board_delta?.conflicts,
    next_move: upload.case_board_delta?.next_move ?? null,
    case_board_delta: upload.case_board_delta ?? null,
    caseBoardRefresh: upload.caseBoardRefresh ?? null,
    requested_document_labels: upload.requested_document_labels,
    counts_toward_gate: upload.document_assessment?.counts_toward_gate ?? null,
  }
}

function ticketUploadToMaterial(
  sessionId: string,
  result: WxUploadTicketUploadResult,
): UploadedMaterial {
  const upload = result.upload
  const fallbackName =
    result.file_name ?? upload.document_type_label ?? upload.document_id ?? "微信聊天文件"
  return uploadResponseToMaterial(sessionId, upload, {
    name: fallbackName,
    mimeType: result.mime_type,
    size: result.size,
    uploadedAt: result.uploaded_at,
  })
}

function mergeMaterials(
  previous: UploadedMaterial[],
  next: UploadedMaterial[],
): UploadedMaterial[] {
  const byId = new Map<string, UploadedMaterial>()
  for (const item of previous) {
    byId.set(item.document_id ?? item.id, item)
  }
  for (const item of next) {
    byId.set(item.document_id ?? item.id, item)
  }
  return Array.from(byId.values()).sort((a, b) =>
    b.uploaded_at.localeCompare(a.uploaded_at),
  )
}

export function useWxWorkbench() {
  const auth = useAuth()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [visaType, setVisaType] = useState<VisaFamily | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [uploadedMaterials, setUploadedMaterials] = useState<UploadedMaterial[]>([])
  const [userReport, setUserReport] = useState<UserReport | null>(null)
  const [availableSessions, setAvailableSessions] = useState<BackendSessionListItem[]>([])
  const [isInitializing, setIsInitializing] = useState(false)
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [isNativeUploadStarting, setIsNativeUploadStarting] = useState(false)
  const [isRefreshingUploadTicket, setIsRefreshingUploadTicket] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [nativeUploadNotice, setNativeUploadNotice] = useState<string | null>(null)

  const refreshReport = useCallback(async (targetSessionId = sessionId) => {
    if (!targetSessionId) {
      return
    }
    try {
      const report = await getUserReport(targetSessionId)
      setUserReport(report)
      setReportError(null)
    } catch (err) {
      const message = err instanceof Error ? err.message : "报告刷新失败"
      setReportError(message)
    }
  }, [sessionId])

  const loadSessionMessages = useCallback(async (targetSessionId: string) => {
    const response = await fetchSessionMessages(targetSessionId)
    setMessages(
      response.messages
        .map(backendMessageToChatMessage)
        .filter((message): message is ChatMessage => Boolean(message)),
    )
  }, [])

  const restoreSession = useCallback(async (item: BackendSessionListItem) => {
    const targetSessionId = item.session_id
    setSessionId(targetSessionId)
    setVisaType(visaFamilyFromBackendFamily(item.declared_family))
    setUploadedMaterials([])
    setChatError(null)
    await loadSessionMessages(targetSessionId)
    await refreshReport(targetSessionId)
  }, [loadSessionMessages, refreshReport])

  useEffect(() => {
    if (auth.isCheckingAuth || !auth.isAuthenticated) {
      return
    }

    let cancelled = false
    const bootstrap = async () => {
      setIsInitializing(true)
      try {
        const response = await listSessions()
        if (cancelled) {
          return
        }
        setAvailableSessions(response.sessions)
        const firstSession = response.sessions[0]
        if (firstSession) {
          await restoreSession(firstSession)
        }
      } catch (err) {
        if (!cancelled) {
          setChatError(err instanceof Error ? err.message : "恢复会话失败")
        }
      } finally {
        if (!cancelled) {
          setIsInitializing(false)
        }
      }
    }

    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [auth.isAuthenticated, auth.isCheckingAuth, restoreSession])

  const startSession = useCallback(async (family: VisaFamily) => {
    setIsCreatingSession(true)
    setChatError(null)
    try {
      const session = await createSession(family)
      setSessionId(session.session_id)
      setVisaType(family)
      setMessages([])
      setUploadedMaterials([])
      setUserReport(null)
      setAvailableSessions((current) => [
        {
          session_id: session.session_id,
          phase_state: session.phase_state,
          declared_family: family,
          current_governor_decision: session.current_governor_decision,
        },
        ...current.filter((item) => item.session_id !== session.session_id),
      ])
      await refreshReport(session.session_id)
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "创建会话失败")
    } finally {
      setIsCreatingSession(false)
    }
  }, [refreshReport])

  const sendTextMessage = useCallback(async (content: string) => {
    const trimmed = content.trim()
    if (!sessionId || !trimmed) {
      return
    }
    const clientMessageId = `wx-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const userMessage: ChatMessage = {
      id: clientMessageId,
      role: "user",
      content: trimmed,
      timestamp: isoNow(),
      status: "sending",
      client_message_id: clientMessageId,
      retry_content: trimmed,
    }
    setMessages((current) => [...current, userMessage])
    setIsSending(true)
    setChatError(null)
    try {
      const response = await sendMessage(sessionId, trimmed, null, clientMessageId)
      setMessages((current) => [
        ...current.map((message) =>
          message.id === clientMessageId ? { ...message, status: "sent" as const } : message,
        ),
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: response.assistant_message,
          timestamp: isoNow(),
          status: "sent",
          public_reasoning: response.public_reasoning ?? null,
        },
      ])
      await refreshReport(sessionId)
    } catch (err) {
      const message = err instanceof Error ? err.message : "发送失败"
      setMessages((current) =>
        current.map((item) =>
          item.id === clientMessageId
            ? { ...item, status: "error" as const, error_detail: message }
            : item,
        ),
      )
      setChatError(message)
    } finally {
      setIsSending(false)
    }
  }, [refreshReport, sessionId])

  const uploadH5Files = useCallback(async (files: File[], contextText?: string) => {
    if (!sessionId || !files.length) {
      return
    }
    setIsUploading(true)
    setUploadError(null)
    try {
      const nextMaterials: UploadedMaterial[] = []
      for (const file of files) {
        const response = await uploadFile(sessionId, file, contextText)
        nextMaterials.push(
          uploadResponseToMaterial(sessionId, response, {
            name: file.name,
            mimeType: file.type,
            size: file.size,
            previewUrl: fileKind(file) === "image" ? URL.createObjectURL(file) : null,
          }),
        )
      }
      setUploadedMaterials((current) => mergeMaterials(current, nextMaterials))
      await refreshReport(sessionId)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败")
    } finally {
      setIsUploading(false)
    }
  }, [refreshReport, sessionId])

  const refreshNativeUploadTicket = useCallback(async (ticket: string, targetSessionId?: string) => {
    setIsRefreshingUploadTicket(true)
    try {
      const status: WxUploadTicketStatusResponse = await getWxUploadTicketStatus(ticket)
      const effectiveSessionId = targetSessionId ?? status.session_id
      const materials = status.upload_results.map((result) =>
        ticketUploadToMaterial(effectiveSessionId, result),
      )
      if (materials.length) {
        setUploadedMaterials((current) => mergeMaterials(current, materials))
        setNativeUploadNotice(`已同步 ${materials.length} 个微信聊天文件。`)
        await refreshReport(effectiveSessionId)
      }
      if (status.status !== "active" || materials.length > 0) {
        clearPendingWxUploadTicket(ticket)
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "同步微信上传结果失败")
    } finally {
      setIsRefreshingUploadTicket(false)
    }
  }, [refreshReport])

  useEffect(() => {
    if (!auth.isAuthenticated) {
      return
    }

    const refreshPending = () => {
      const pending = readPendingWxUploadTicket()
      if (pending) {
        void refreshNativeUploadTicket(pending.ticket, pending.sessionId)
      }
    }

    refreshPending()
    window.addEventListener("pageshow", refreshPending)
    window.addEventListener("visibilitychange", refreshPending)
    return () => {
      window.removeEventListener("pageshow", refreshPending)
      window.removeEventListener("visibilitychange", refreshPending)
    }
  }, [auth.isAuthenticated, refreshNativeUploadTicket])

  const startNativeWechatUpload = useCallback(async () => {
    if (!sessionId) {
      setUploadError("请先创建会话再上传材料。")
      return
    }
    setIsNativeUploadStarting(true)
    setUploadError(null)
    setNativeUploadNotice(null)
    try {
      const ticket = await createWxUploadTicket(sessionId)
      storePendingWxUploadTicket({
        ticket: ticket.ticket,
        sessionId,
        createdAt: Date.now(),
      })
      const result = await navigateToNativeUploadPage({
        sessionId,
        ticket: ticket.ticket,
        apiBaseUrl: resolveWxApiBaseUrl(),
      })
      if (!result.ok) {
        setNativeUploadNotice("当前不是微信小程序环境，请在小程序 web-view 内使用聊天文件上传。")
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "启动微信上传失败")
    } finally {
      setIsNativeUploadStarting(false)
    }
  }, [sessionId])

  const resetToVisaPicker = useCallback(() => {
    setSessionId(null)
    setVisaType(null)
    setMessages([])
    setUploadedMaterials([])
    setUserReport(null)
    setChatError(null)
    setReportError(null)
    setUploadError(null)
  }, [])

  const quotaLabel = useMemo(() => {
    const quota = auth.accessKeyQuota
    if (!quota) {
      return null
    }
    return `剩余 ${quota.remaining_uses}/${quota.usage_limit} 次`
  }, [auth.accessKeyQuota])

  return {
    auth,
    sessionId,
    visaType,
    messages,
    uploadedMaterials,
    userReport,
    availableSessions,
    isInitializing,
    isCreatingSession,
    isSending,
    isUploading,
    isNativeUploadStarting,
    isRefreshingUploadTicket,
    chatError,
    reportError,
    uploadError,
    nativeUploadNotice,
    quotaLabel,
    startSession,
    restoreSession,
    sendTextMessage,
    uploadH5Files,
    startNativeWechatUpload,
    refreshNativeUploadTicket,
    refreshReport,
    resetToVisaPicker,
  }
}
