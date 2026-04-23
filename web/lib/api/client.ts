import { buildApiUrl } from "./config"
import {
  mapFileUploadResponse,
  mapMessageResponse,
  mapRequiredPackage,
  mapSession,
  toVisaFamilyCode,
  mapUserReport,
} from "./mappers"
import type {
  BackendFileUploadResponse,
  BackendInternalReport,
  BackendMessageResponse,
  BackendRequiredPackage,
  BackendSession,
  BackendUserReport,
  FileUploadResponse,
  InternalReport,
  MessageResponse,
  RequiredPackage,
  Session,
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

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
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

export async function createSession(visaFamily: VisaFamily): Promise<Session> {
  const response = await fetch(buildApiUrl("/v1/sessions"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      declared_family: toVisaFamilyCode(visaFamily),
    }),
  })

  return mapSession(await handleResponse<BackendSession>(response))
}

export async function getRequiredPackage(sessionId: string): Promise<RequiredPackage> {
  const response = await fetch(
    buildApiUrl(`/v1/sessions/${sessionId}/required-package`),
  )
  return mapRequiredPackage(await handleResponse<BackendRequiredPackage>(response))
}

export async function sendMessage(
  sessionId: string,
  content: string,
): Promise<MessageResponse> {
  const response = await fetch(buildApiUrl(`/v1/sessions/${sessionId}/messages`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      role: "user",
      content,
    }),
  })

  return mapMessageResponse(await handleResponse<BackendMessageResponse>(response))
}

export async function getUserReport(sessionId: string): Promise<UserReport> {
  const response = await fetch(buildApiUrl(`/v1/sessions/${sessionId}/reports/user`))
  return mapUserReport(await handleResponse<BackendUserReport>(response))
}

export async function getInternalReport(sessionId: string): Promise<InternalReport> {
  const response = await fetch(
    buildApiUrl(`/v1/sessions/${sessionId}/reports/internal`),
  )
  return handleResponse<BackendInternalReport>(response)
}

export async function uploadFile(
  sessionId: string,
  file: File,
): Promise<FileUploadResponse> {
  const formData = new FormData()
  formData.append("file", file)

  const response = await fetch(buildApiUrl(`/v1/sessions/${sessionId}/files`), {
    method: "POST",
    body: formData,
  })

  return mapFileUploadResponse(
    await handleResponse<BackendFileUploadResponse>(response),
  )
}

export { ApiError }
