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
  return `${baseUrl}${path}`
}

export function isMockModeEnabled(): boolean {
  return process.env.NEXT_PUBLIC_MOCK === "true"
}
