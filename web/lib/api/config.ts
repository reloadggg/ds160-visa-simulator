const DEFAULT_API_BASE_URL = "/api"

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, "")
}

export function getApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim()
  return normalizeBaseUrl(configured || DEFAULT_API_BASE_URL)
}

export function buildApiUrl(path: string): string {
  const baseUrl = getApiBaseUrl()
  const normalizedPath = path.startsWith("/") ? path : `/${path}`
  return `${baseUrl}${normalizedPath}`
}

/**
 * Rewrite backend content URLs so they resolve under NEXT_PUBLIC_API_BASE_URL.
 * Backend returns paths like `/v1/sessions/{id}/files/{doc}/content` which break
 * when the browser base is `/api` unless rewritten via buildApiUrl.
 */
export function rewriteBackendContentUrl(
  contentUrl?: string | null,
  options?: { sessionId?: string | null; documentId?: string | null },
): string | null {
  const sessionId = options?.sessionId ?? null
  const documentId = options?.documentId ?? null

  if (typeof contentUrl === "string" && contentUrl.trim()) {
    const trimmed = contentUrl.trim()

    // Absolute remote URLs stay as-is (signed CDN, etc.).
    if (/^https?:\/\//i.test(trimmed)) {
      return trimmed
    }

    const baseUrl = getApiBaseUrl()
    if (trimmed === baseUrl || trimmed.startsWith(`${baseUrl}/`)) {
      return trimmed
    }

    // Backend relative API path: `/v1/...`
    if (trimmed.startsWith("/v1/") || trimmed === "/v1") {
      return buildApiUrl(trimmed)
    }

    // Already-rooted path that is not under /v1 — leave alone.
    if (trimmed.startsWith("/")) {
      return trimmed
    }

    // Bare relative fragment
    return buildApiUrl(`/${trimmed.replace(/^\/+/, "")}`)
  }

  if (sessionId && documentId) {
    return buildApiUrl(
      `/v1/sessions/${sessionId}/files/${documentId}/content`,
    )
  }

  return null
}

export function isMockModeEnabled(): boolean {
  return process.env.NEXT_PUBLIC_MOCK === "true"
}
