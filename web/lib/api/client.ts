import { buildApiUrl } from "./config"
import {
  mapFileUploadResponse,
  mapInterviewReviewResponse,
  mapMessageResponse,
  mapRequiredPackage,
  mapSession,
  toVisaFamilyCode,
  mapUserReport,
} from "./mappers"
import type {
  AuthResponse,
  AuthStatusResponse,
  BackendFileUploadResponse,
  BackendInternalReport,
  BackendMessageResponse,
  BackendRequiredPackage,
  BackendSession,
  BackendUserReport,
  DebugMaterialBundleResponse,
  DebugMaterialBundleScenario,
  DebugMaterialBundleStreamEvent,
  DebugFillResponse,
  FileUploadResponse,
  InternalReport,
  InterviewReviewResponse,
  MessageResponse,
  MessageStreamEvent,
  ModelListResponse,
  RagUploadMetadata,
  RagStatus,
  RagUploadResponse,
  RequiredPackage,
  Session,
  SessionExportPayload,
  UserModelRuntimeConfig,
  UserReport,
  VisaFamily,
} from "./types"

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data?: unknown,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

function extractErrorMessage(data: unknown, fallback: string): string {
  if (
    typeof data === "object" &&
    data !== null &&
    "detail" in data &&
    typeof (data as { detail?: unknown }).detail === "string"
  ) {
    return (data as { detail: string }).detail
  }

  if (typeof data === "string" && data.trim()) {
    return data
  }

  return fallback
}

function getAuthHeaders(contentType?: string): HeadersInit {
  const headers: Record<string, string> = {}

  if (contentType) {
    headers["Content-Type"] = contentType
  }

  return headers
}

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: "include",
  })
}

function toBackendModelConfig(config?: UserModelRuntimeConfig | null) {
  if (!config) {
    return undefined
  }
  return {
    base_url: config.base_url,
    api_key: config.api_key,
    model: config.model,
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("auth:unauthorized"))
    }

    let errorData: unknown
    try {
      errorData = await response.json()
    } catch {
      errorData = await response.text()
    }

    throw new ApiError(
      extractErrorMessage(
        errorData,
        `请求失败：${response.status} ${response.statusText}`,
      ),
      response.status,
      errorData,
    )
  }

  return response.json() as Promise<T>
}

export async function login(password: string): Promise<AuthResponse> {
  const response = await apiFetch(buildApiUrl("/v1/auth/login"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ password }),
  })

  return handleResponse<AuthResponse>(response)
}

export async function getAuthStatus(): Promise<AuthStatusResponse> {
  const response = await apiFetch(buildApiUrl("/v1/auth/me"))
  return handleResponse<AuthStatusResponse>(response)
}

export async function logout(): Promise<void> {
  await apiFetch(buildApiUrl("/v1/auth/logout"), {
    method: "POST",
    headers: getAuthHeaders(),
  }).catch(() => undefined)
}

export async function createSession(visaFamily: VisaFamily): Promise<Session> {
  const response = await apiFetch(buildApiUrl("/v1/sessions"), {
    method: "POST",
    headers: getAuthHeaders("application/json"),
    body: JSON.stringify({
      declared_family: toVisaFamilyCode(visaFamily),
    }),
  })

  return mapSession(await handleResponse<BackendSession>(response))
}

export async function getRequiredPackage(sessionId: string): Promise<RequiredPackage> {
  const response = await apiFetch(
    buildApiUrl(`/v1/sessions/${sessionId}/required-package`),
    { headers: getAuthHeaders() }
  )
  return mapRequiredPackage(await handleResponse<BackendRequiredPackage>(response))
}

export async function sendMessage(
  sessionId: string,
  content: string,
  modelConfig?: UserModelRuntimeConfig | null,
  clientMessageId?: string,
): Promise<MessageResponse> {
  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/messages`), {
    method: "POST",
    headers: getAuthHeaders("application/json"),
    body: JSON.stringify({
      role: "user",
      content,
      client_message_id: clientMessageId,
      model_config: toBackendModelConfig(modelConfig),
    }),
  })

  return mapMessageResponse(await handleResponse<BackendMessageResponse>(response))
}

export async function sendMessageStream(
  sessionId: string,
  content: string,
  modelConfig: UserModelRuntimeConfig | null,
  clientMessageId: string | undefined,
  onEvent: (event: MessageStreamEvent) => void,
): Promise<MessageResponse> {
  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/messages/stream`), {
    method: "POST",
    headers: getAuthHeaders("application/json"),
    body: JSON.stringify({
      role: "user",
      content,
      client_message_id: clientMessageId,
      model_config: toBackendModelConfig(modelConfig),
    }),
  })

  if (!response.ok) {
    return mapMessageResponse(await handleResponse<BackendMessageResponse>(response))
  }
  if (!response.body) {
    throw new ApiError("当前浏览器不支持读取流式响应。", 0)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let finalPayload: BackendMessageResponse | null = null

  const processChunk = (chunk: string) => {
    buffer += chunk
    const events = buffer.split("\n\n")
    buffer = events.pop() ?? ""
    for (const rawEvent of events) {
      const parsed = parseSseEvent(rawEvent)
      if (!parsed) {
        continue
      }
      onEvent(parsed)
      if (parsed.event === "final") {
        finalPayload = parsed.data
      }
      if (parsed.event === "error") {
        throw new ApiError(parsed.data.detail ?? "流式消息处理失败。", parsed.data.status ?? 500, parsed.data)
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    processChunk(decoder.decode(value, { stream: true }))
  }
  processChunk(decoder.decode())

  if (!finalPayload) {
    throw new ApiError("流式响应没有返回最终消息。", 502)
  }
  return mapMessageResponse(finalPayload)
}

function parseRawSseEvent(rawEvent: string): { event: string; data: unknown } | null {
  const eventLine = rawEvent
    .split("\n")
    .find((line) => line.startsWith("event:"))
  const dataLine = rawEvent
    .split("\n")
    .find((line) => line.startsWith("data:"))
  if (!eventLine || !dataLine) {
    return null
  }
  const event = eventLine.slice("event:".length).trim()
  const data = JSON.parse(dataLine.slice("data:".length).trim()) as unknown
  return { event, data }
}

function parseSseEvent(rawEvent: string): MessageStreamEvent | null {
  const parsed = parseRawSseEvent(rawEvent)
  if (!parsed) {
    return null
  }
  const { event, data } = parsed
  if (event === "accepted" || event === "analyzing") {
    return { event, data: data as Record<string, unknown> }
  }
  if (event === "final") {
    return { event, data: data as BackendMessageResponse }
  }
  if (event === "error") {
    return { event, data: data as { status?: number; detail?: string } }
  }
  return null
}

function parseDebugMaterialBundleSseEvent(rawEvent: string): DebugMaterialBundleStreamEvent | null {
  const parsed = parseRawSseEvent(rawEvent)
  if (!parsed) {
    return null
  }
  const { event, data } = parsed
  if (
    event === "accepted" ||
    event === "debug_bundle_started" ||
    event === "document_created" ||
    event === "evidence_written" ||
    event === "profile_recomputed" ||
    event === "gate_refreshed" ||
    event === "document_review_started" ||
    event === "governor_decided" ||
    event === "final" ||
    event === "error"
  ) {
    return { event, data } as DebugMaterialBundleStreamEvent
  }
  return null
}

export async function listUserModels(
  baseUrl: string,
  apiKey: string,
): Promise<ModelListResponse> {
  const response = await apiFetch(buildApiUrl("/v1/model-config/models"), {
    method: "POST",
    headers: getAuthHeaders("application/json"),
    body: JSON.stringify({
      base_url: baseUrl,
      api_key: apiKey,
    }),
  })

  return handleResponse<ModelListResponse>(response)
}

export async function getRagStatus(): Promise<RagStatus> {
  const response = await apiFetch(buildApiUrl("/v1/rag/status"), {
    headers: getAuthHeaders(),
  })

  return handleResponse<RagStatus>(response)
}

export async function uploadRagFile(
  file: File,
  metadata: RagUploadMetadata = {},
): Promise<RagUploadResponse> {
  const formData = new FormData()
  formData.append("file", file)

  const fields: RagUploadMetadata = {
    ...metadata,
    title: metadata.title?.trim() || file.name,
  }
  Object.entries(fields).forEach(([key, value]) => {
    const normalized = value?.trim()
    if (normalized) {
      formData.append(key, normalized)
    }
  })

  const response = await apiFetch(buildApiUrl("/v1/rag/files"), {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData,
  })

  return handleResponse<RagUploadResponse>(response)
}

export async function getUserReport(sessionId: string): Promise<UserReport> {
  const response = await apiFetch(
    buildApiUrl(`/v1/sessions/${sessionId}/reports/user`),
    { headers: getAuthHeaders() }
  )
  return mapUserReport(await handleResponse<BackendUserReport>(response))
}

export async function getInternalReport(sessionId: string): Promise<InternalReport> {
  const response = await apiFetch(
    buildApiUrl(`/v1/sessions/${sessionId}/reports/internal`),
    { headers: getAuthHeaders() }
  )
  return handleResponse<BackendInternalReport>(response)
}

export async function exportSession(sessionId: string): Promise<SessionExportPayload> {
  const response = await apiFetch(
    buildApiUrl(`/v1/sessions/${sessionId}/reports/export`),
    { headers: getAuthHeaders() }
  )
  return handleResponse<SessionExportPayload>(response)
}

export async function generateInterviewReview(sessionId: string): Promise<InterviewReviewResponse> {
  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/reports/review`), {
    method: "POST",
    headers: getAuthHeaders(),
  })
  return mapInterviewReviewResponse(await handleResponse<InterviewReviewResponse>(response))
}

export async function debugFillCurrentGap(
  sessionId: string,
  scenario = "normal",
): Promise<DebugFillResponse> {
  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/debug/fill-current-gap`), {
    method: "POST",
    headers: {
      ...getAuthHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ scenario }),
  })
  return handleResponse<DebugFillResponse>(response)
}

export async function createDebugMaterialBundle(
  sessionId: string,
  scenario: DebugMaterialBundleScenario | string,
  includeSyntheticUserTurns = true,
): Promise<DebugMaterialBundleResponse> {
  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/debug/material-bundles`), {
    method: "POST",
    headers: getAuthHeaders("application/json"),
    body: JSON.stringify({
      scenario,
      include_synthetic_user_turns: includeSyntheticUserTurns,
    }),
  })
  return handleResponse<DebugMaterialBundleResponse>(response)
}

export async function createDebugMaterialBundleStream(
  sessionId: string,
  scenario: DebugMaterialBundleScenario | string,
  includeSyntheticUserTurns: boolean,
  onEvent: (event: DebugMaterialBundleStreamEvent) => void,
): Promise<DebugMaterialBundleResponse> {
  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/debug/material-bundles/stream`), {
    method: "POST",
    headers: getAuthHeaders("application/json"),
    body: JSON.stringify({
      scenario,
      include_synthetic_user_turns: includeSyntheticUserTurns,
    }),
  })

  if (!response.ok) {
    return handleResponse<DebugMaterialBundleResponse>(response)
  }
  if (!response.body) {
    return createDebugMaterialBundle(sessionId, scenario, includeSyntheticUserTurns)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let finalPayload: DebugMaterialBundleResponse | null = null

  const processChunk = (chunk: string) => {
    buffer += chunk
    const events = buffer.split("\n\n")
    buffer = events.pop() ?? ""
    for (const rawEvent of events) {
      const parsed = parseDebugMaterialBundleSseEvent(rawEvent)
      if (!parsed) {
        continue
      }
      onEvent(parsed)
      if (parsed.event === "final") {
        finalPayload = parsed.data
      }
      if (parsed.event === "error") {
        throw new ApiError(
          parsed.data.detail ?? "调试材料包生成失败。",
          parsed.data.status ?? 500,
          parsed.data,
        )
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    processChunk(decoder.decode(value, { stream: true }))
  }
  processChunk(decoder.decode())

  if (!finalPayload) {
    throw new ApiError("流式材料包响应没有返回最终结果。", 502)
  }
  return finalPayload
}

export async function uploadFile(
  sessionId: string,
  file: File,
  contextText?: string,
): Promise<FileUploadResponse> {
  const formData = new FormData()
  formData.append("file", file)
  if (contextText) {
    formData.append("context_text", contextText)
  }

  const response = await apiFetch(buildApiUrl(`/v1/sessions/${sessionId}/files`), {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData,
  })

  return mapFileUploadResponse(
    await handleResponse<BackendFileUploadResponse>(response),
  )
}

export function getFileContentUrl(sessionId: string, documentId: string): string {
  return buildApiUrl(`/v1/sessions/${sessionId}/files/${documentId}/content`)
}

export { ApiError }
