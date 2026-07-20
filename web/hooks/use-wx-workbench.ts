"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useAuth } from "@/hooks/use-auth"
import {
  createSession,
  createWxUploadTicket,
  fetchSessionMessages,
  getFileContentUrl,
  getUserReport,
  getWxUploadTicketStatus,
  listSessionDocuments,
  listSessions,
  sendMessage,
  uploadFile,
} from "@/lib/api/client"
import type {
  BackendSessionListItem,
  BackendSessionMessage,
  ChatMessage,
  FileUploadResponse,
  SessionDocumentListItem,
  UploadedMaterial,
  UserReport,
  VisaFamily,
  WxUploadTicketStatusResponse,
  WxUploadTicketUploadResult,
} from "@/lib/api/types"
import { mapSessionDocumentsToUploadedMaterials } from "@/lib/api/mappers"
import {
  isTerminalMaterialUnderstandingStatus,
  materialUnderstandingStatus,
} from "@/lib/upload-feedback-policy"
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

/** ~2.5 min total backoff for material understanding poll (F5 / F13). */
const MATERIAL_UNDERSTANDING_POLL_DELAYS_MS = [
  1000, 2000, 4000, 8000, 15000, 20000, 30000, 30000, 30000, 30000,
]

function isoNow(): string {
  return new Date().toISOString()
}

function waitForMilliseconds(delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, delayMs)
  })
}

function revokeIfObjectUrl(url?: string | null): void {
  if (url && url.startsWith("blob:")) {
    try {
      URL.revokeObjectURL(url)
    } catch {
      // ignore revoke failures
    }
  }
}

function revokeMaterialObjectUrls(materials: UploadedMaterial[]): void {
  for (const material of materials) {
    revokeIfObjectUrl(material.preview_url)
  }
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

function isClosedSession(item: BackendSessionListItem): boolean {
  if (item.phase_state === "completed" || item.phase_state === "session_closed") {
    return true
  }
  return ["passed", "refused", "simulated_refusal"].includes(
    item.current_governor_decision ?? "",
  )
}

function isTerminalInterviewState(
  phaseState: string | null | undefined,
  governorDecision: string | null | undefined,
  userReport: UserReport | null,
): boolean {
  return (
    phaseState === "completed" ||
    phaseState === "session_closed" ||
    ["passed", "refused", "simulated_refusal"].includes(governorDecision ?? "") ||
    userReport?.interview_result === "passed" ||
    userReport?.interview_result === "refused" ||
    userReport?.interview_status === "simulated_refusal"
  )
}

function documentNeedsUnderstandingPoll(
  doc: Pick<SessionDocumentListItem, "understanding_status" | "status">,
): boolean {
  const status = doc.understanding_status ?? doc.status ?? null
  if (isTerminalMaterialUnderstandingStatus(status)) {
    return false
  }
  return (
    status === "queued" ||
    status === "processing" ||
    status === "waiting_for_parse" ||
    status === "parsing" ||
    status === "uploaded" ||
    !status
  )
}

function responseNeedsMaterialUnderstandingRefresh(
  response: FileUploadResponse,
): boolean {
  const status =
    materialUnderstandingStatus(response) ??
    response.understanding_status ??
    response.job_status ??
    null
  if (isTerminalMaterialUnderstandingStatus(status)) {
    return false
  }
  return (
    status === "queued" ||
    status === "processing" ||
    status === "waiting_for_parse" ||
    status === "parsing"
  )
}

function kindFromNameAndMime(
  name: string,
  mimeType?: string | null,
): UploadedMaterial["kind"] {
  const mime = mimeType ?? ""
  if (mime.startsWith("image/")) {
    return "image"
  }
  if (mime === "application/pdf" || name.toLowerCase().endsWith(".pdf")) {
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
  const kind = kindFromNameAndMime(source.name, mimeType)
  const contentUrl = upload.document_id
    ? getFileContentUrl(sessionId, upload.document_id)
    : null

  return {
    id: documentId,
    session_id: sessionId,
    name: source.name,
    mime_type: mimeType,
    kind,
    size: source.size ?? undefined,
    preview_url: source.previewUrl ?? (kind === "image" ? contentUrl : null),
    content_url: contentUrl,
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
    const key = item.document_id ?? item.id
    const existing = byId.get(key)
    if (!existing) {
      byId.set(key, item)
      continue
    }

    const contentUrl =
      item.document_id && (item.session_id ?? existing.session_id)
        ? getFileContentUrl(
            item.session_id ?? existing.session_id ?? "",
            item.document_id,
          )
        : item.content_url ?? existing.content_url ?? null

    let previewUrl = item.preview_url ?? existing.preview_url ?? null
    const existingBlob =
      existing.preview_url?.startsWith("blob:") ? existing.preview_url : null
    if (contentUrl && (item.kind ?? existing.kind) === "image") {
      if (existingBlob) {
        revokeIfObjectUrl(existingBlob)
      }
      previewUrl = contentUrl
    } else if (item.preview_url && !item.preview_url.startsWith("blob:") && existingBlob) {
      revokeIfObjectUrl(existingBlob)
      previewUrl = item.preview_url
    } else if (existingBlob && !item.preview_url) {
      previewUrl = existingBlob
    }

    byId.set(key, {
      ...existing,
      ...item,
      content_url: contentUrl,
      preview_url: previewUrl,
      understanding_status:
        item.understanding_status ?? existing.understanding_status ?? null,
      status_label: item.status_label || existing.status_label,
    })
  }
  return Array.from(byId.values()).sort((a, b) =>
    b.uploaded_at.localeCompare(a.uploaded_at),
  )
}

type SendTextOptions = {
  reuseMessageId?: string
  clientMessageId?: string
}

export function useWxWorkbench() {
  const auth = useAuth()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [phaseState, setPhaseState] = useState<string | null>(null)
  const [governorDecision, setGovernorDecision] = useState<string | null>(null)
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

  const sessionIdRef = useRef<string | null>(null)
  const sendingRef = useRef(false)
  const materialsRef = useRef<UploadedMaterial[]>([])

  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    materialsRef.current = uploadedMaterials
  }, [uploadedMaterials])

  // Revoke any remaining blob: object URLs on unmount (F17).
  useEffect(() => {
    return () => {
      revokeMaterialObjectUrls(materialsRef.current)
    }
  }, [])

  const isSessionTerminal = useMemo(
    () => isTerminalInterviewState(phaseState, governorDecision, userReport),
    [governorDecision, phaseState, userReport],
  )

  const refreshReport = useCallback(async (targetSessionId?: string | null) => {
    const effectiveSessionId = targetSessionId ?? sessionIdRef.current
    if (!effectiveSessionId) {
      return
    }
    try {
      const report = await getUserReport(effectiveSessionId)
      if (sessionIdRef.current !== effectiveSessionId) {
        return
      }
      setUserReport(report)
      setReportError(null)
      if (report.interview_result) {
        // Keep local terminal signals in sync with report.
        if (
          report.interview_result === "passed" ||
          report.interview_result === "refused"
        ) {
          setGovernorDecision(report.interview_result)
        } else if (report.interview_status === "simulated_refusal") {
          setGovernorDecision("simulated_refusal")
        }
      }
    } catch (err) {
      if (sessionIdRef.current !== effectiveSessionId) {
        return
      }
      const message = err instanceof Error ? err.message : "报告刷新失败"
      setReportError(message)
    }
  }, [])

  const loadSessionMessages = useCallback(async (targetSessionId: string) => {
    const response = await fetchSessionMessages(targetSessionId)
    if (sessionIdRef.current !== targetSessionId) {
      return
    }
    setMessages(
      response.messages
        .map(backendMessageToChatMessage)
        .filter((message): message is ChatMessage => Boolean(message)),
    )
  }, [])

  const loadSessionDocuments = useCallback(async (targetSessionId: string) => {
    const response = await listSessionDocuments(targetSessionId)
    if (sessionIdRef.current !== targetSessionId) {
      return
    }
    const materials = mapSessionDocumentsToUploadedMaterials(response, {
      sessionId: targetSessionId,
    })
    setUploadedMaterials((current) => {
      revokeMaterialObjectUrls(
        current.filter(
          (item) =>
            item.preview_url?.startsWith("blob:") &&
            !materials.some(
              (next) => (next.document_id ?? next.id) === (item.document_id ?? item.id),
            ),
        ),
      )
      return mergeMaterials([], materials)
    })
  }, [])

  const queueMaterialUnderstandingRefresh = useCallback(
    (targetSessionId: string, documentIds: string[]) => {
      const tracked = new Set(documentIds.filter(Boolean))
      if (!tracked.size) {
        return
      }

      void (async () => {
        let sawTerminal = false
        for (const delayMs of MATERIAL_UNDERSTANDING_POLL_DELAYS_MS) {
          await waitForMilliseconds(delayMs)
          if (sessionIdRef.current !== targetSessionId) {
            return
          }
          try {
            const list = await listSessionDocuments(targetSessionId)
            if (sessionIdRef.current !== targetSessionId) {
              return
            }
            const relevant = list.documents.filter(
              (doc) => doc.document_id && tracked.has(doc.document_id) && !doc.tombstoned,
            )
            if (relevant.length) {
              const nextMaterials = mapSessionDocumentsToUploadedMaterials(
                { ...list, documents: relevant },
                { sessionId: targetSessionId },
              )
              setUploadedMaterials((current) =>
                mergeMaterials(current, nextMaterials),
              )
            }

            const pending = relevant.filter(documentNeedsUnderstandingPoll)
            const terminalDocs = relevant.filter((doc) =>
              isTerminalMaterialUnderstandingStatus(
                doc.understanding_status ?? doc.status ?? null,
              ),
            )
            if (terminalDocs.length) {
              sawTerminal = true
            }
            if (!pending.length) {
              if (sawTerminal || terminalDocs.length) {
                await refreshReport(targetSessionId)
              }
              return
            }
          } catch {
            // keep polling through transient list failures
          }
        }
        if (sessionIdRef.current === targetSessionId) {
          await refreshReport(targetSessionId)
        }
      })()
    },
    [refreshReport],
  )

  const restoreSession = useCallback(async (item: BackendSessionListItem) => {
    const targetSessionId = item.session_id
    revokeMaterialObjectUrls(materialsRef.current)
    sessionIdRef.current = targetSessionId
    setSessionId(targetSessionId)
    setPhaseState(item.phase_state ?? null)
    setGovernorDecision(item.current_governor_decision ?? null)
    setVisaType(visaFamilyFromBackendFamily(item.declared_family))
    setUploadedMaterials([])
    setChatError(null)
    await loadSessionMessages(targetSessionId)
    try {
      await loadSessionDocuments(targetSessionId)
    } catch {
      // restore materials is best-effort; chat still works
    }
    await refreshReport(targetSessionId)
  }, [loadSessionDocuments, loadSessionMessages, refreshReport])

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
        const activeSessions = response.sessions.filter((item) => !isClosedSession(item))
        setAvailableSessions(activeSessions)
        const firstSession = activeSessions[0]
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
      revokeMaterialObjectUrls(materialsRef.current)
      sessionIdRef.current = session.session_id
      setSessionId(session.session_id)
      setPhaseState(session.phase_state ?? null)
      setGovernorDecision(session.current_governor_decision ?? null)
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

  const sendTextMessage = useCallback(async (
    content: string,
    options?: SendTextOptions,
  ) => {
    const trimmed = content.trim()
    if (!sessionId || !trimmed) {
      return
    }
    if (sendingRef.current || isSending) {
      return
    }
    if (isTerminalInterviewState(phaseState, governorDecision, userReport)) {
      setChatError("本轮面签已结束，不能继续发送消息。")
      return
    }

    const clientMessageId =
      options?.clientMessageId ??
      `wx-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const reuseMessageId = options?.reuseMessageId

    sendingRef.current = true
    setIsSending(true)
    setChatError(null)

    if (reuseMessageId) {
      setMessages((current) =>
        current.map((message) =>
          message.id === reuseMessageId
            ? {
                ...message,
                content: trimmed,
                status: "sending" as const,
                error_detail: null,
                client_message_id: clientMessageId,
                retry_content: trimmed,
              }
            : message,
        ),
      )
    } else {
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
    }

    const trackId = reuseMessageId ?? clientMessageId

    try {
      const response = await sendMessage(sessionId, trimmed, null, clientMessageId)
      if (sessionIdRef.current !== sessionId) {
        return
      }
      if (response.governor_decision) {
        setGovernorDecision(response.governor_decision)
        if (
          ["passed", "refused", "simulated_refusal"].includes(
            response.governor_decision,
          )
        ) {
          setPhaseState("completed")
        }
      }
      setMessages((current) => [
        ...current.map((message) =>
          message.id === trackId ? { ...message, status: "sent" as const } : message,
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
      if (sessionIdRef.current !== sessionId) {
        return
      }
      const message = err instanceof Error ? err.message : "发送失败"
      setMessages((current) =>
        current.map((item) =>
          item.id === trackId
            ? { ...item, status: "error" as const, error_detail: message }
            : item,
        ),
      )
      setChatError(message)
    } finally {
      sendingRef.current = false
      setIsSending(false)
    }
  }, [governorDecision, isSending, phaseState, refreshReport, sessionId, userReport])

  const retryMessage = useCallback(
    (message: ChatMessage) => {
      const retryContent = (message.retry_content ?? message.content).trim()
      if (!retryContent) {
        setChatError("这条失败消息没有可重试的文本内容。")
        return
      }
      void sendTextMessage(retryContent, {
        reuseMessageId: message.id,
        clientMessageId: message.client_message_id ?? undefined,
      })
    },
    [sendTextMessage],
  )

  const uploadH5Files = useCallback(async (files: File[], contextText?: string) => {
    if (!sessionId || !files.length) {
      return
    }
    if (isTerminalInterviewState(phaseState, governorDecision, userReport)) {
      setUploadError("本轮面签已结束，不能继续上传材料。")
      return
    }
    setIsUploading(true)
    setUploadError(null)
    try {
      const nextMaterials: UploadedMaterial[] = []
      const uploadedIds: string[] = []
      const uploadResponses: FileUploadResponse[] = []
      for (const file of files) {
        const response = await uploadFile(sessionId, file, contextText)
        uploadResponses.push(response)
        if (response.document_id) {
          uploadedIds.push(response.document_id)
        }
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
      if (
        uploadedIds.length &&
        uploadResponses.some(responseNeedsMaterialUnderstandingRefresh)
      ) {
        queueMaterialUnderstandingRefresh(sessionId, uploadedIds)
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败")
    } finally {
      setIsUploading(false)
    }
  }, [
    governorDecision,
    phaseState,
    queueMaterialUnderstandingRefresh,
    refreshReport,
    sessionId,
    userReport,
  ])

  const refreshNativeUploadTicket = useCallback(async (ticket: string, targetSessionId?: string) => {
    setIsRefreshingUploadTicket(true)
    try {
      const status: WxUploadTicketStatusResponse = await getWxUploadTicketStatus(ticket)
      const effectiveSessionId = targetSessionId ?? status.session_id
      if (
        sessionIdRef.current &&
        sessionIdRef.current !== effectiveSessionId
      ) {
        // ticket belongs to another session; still apply if no active session switch mid-flight
      }
      const materials = status.upload_results.map((result) =>
        ticketUploadToMaterial(effectiveSessionId, result),
      )
      if (materials.length) {
        setUploadedMaterials((current) => mergeMaterials(current, materials))
        setNativeUploadNotice(`已同步 ${materials.length} 个微信聊天文件。`)
        await refreshReport(effectiveSessionId)
        const docIds = materials
          .map((item) => item.document_id)
          .filter((id): id is string => Boolean(id))
        const needsPoll = status.upload_results.some((result) =>
          responseNeedsMaterialUnderstandingRefresh(result.upload),
        )
        if (docIds.length && needsPoll) {
          queueMaterialUnderstandingRefresh(effectiveSessionId, docIds)
        }
      }
      if (status.status !== "active" || materials.length > 0) {
        clearPendingWxUploadTicket(ticket)
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "同步微信上传结果失败")
    } finally {
      setIsRefreshingUploadTicket(false)
    }
  }, [queueMaterialUnderstandingRefresh, refreshReport])

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
    if (isTerminalInterviewState(phaseState, governorDecision, userReport)) {
      setUploadError("本轮面签已结束，不能继续上传材料。")
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
  }, [governorDecision, phaseState, sessionId, userReport])

  const resetToVisaPicker = useCallback(() => {
    revokeMaterialObjectUrls(materialsRef.current)
    sessionIdRef.current = null
    setSessionId(null)
    setPhaseState(null)
    setGovernorDecision(null)
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
    isSessionTerminal,
    chatError,
    reportError,
    uploadError,
    nativeUploadNotice,
    quotaLabel,
    startSession,
    restoreSession,
    sendTextMessage,
    retryMessage,
    uploadH5Files,
    startNativeWechatUpload,
    refreshNativeUploadTicket,
    refreshReport,
    resetToVisaPicker,
  }
}
