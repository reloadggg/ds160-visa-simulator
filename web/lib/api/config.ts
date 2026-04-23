const DEV_FALLBACK_API_BASE_URL = "http://127.0.0.1:8000"

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, "")
}

export function getApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim()
  if (configured) {
    return normalizeBaseUrl(configured)
  }

  if (process.env.NODE_ENV !== "production") {
    return DEV_FALLBACK_API_BASE_URL
  }

  return ""
}

export function buildApiUrl(path: string): string {
  const baseUrl = getApiBaseUrl()
  if (!baseUrl) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL 未配置，无法连接后端接口。")
  }
  return `${baseUrl}${path}`
}

export function isMockModeEnabled(): boolean {
  return process.env.NEXT_PUBLIC_MOCK === "true"
}
