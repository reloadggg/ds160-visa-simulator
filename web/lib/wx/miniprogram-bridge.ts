import { getApiBaseUrl } from "@/lib/api/config"

export interface NativeUploadNavigationParams {
  sessionId: string
  ticket: string
  apiBaseUrl?: string
}

export interface NativeUploadNavigationResult {
  ok: boolean
  url: string
  reason?: "not_browser" | "not_miniprogram" | "bridge_unavailable"
}

interface MiniProgramBridge {
  getEnv?: (callback: (env: { miniprogram: boolean }) => void) => void
  navigateTo?: (options: { url: string; success?: () => void; fail?: () => void }) => void
}

interface WeChatWindow extends Window {
  wx?: {
    miniProgram?: MiniProgramBridge
  }
  __wxjs_environment?: string
}

function getWeChatWindow(): WeChatWindow | null {
  if (typeof window === "undefined") {
    return null
  }
  return window as WeChatWindow
}

function getBridge(): MiniProgramBridge | null {
  return getWeChatWindow()?.wx?.miniProgram ?? null
}

export function resolveWxApiBaseUrl(): string {
  const configured = getApiBaseUrl()
  if (/^https?:\/\//i.test(configured)) {
    return configured
  }
  if (typeof window === "undefined") {
    return configured
  }
  const normalizedPath = configured.startsWith("/") ? configured : `/${configured}`
  return `${window.location.origin}${normalizedPath}`
}

export function buildNativeUploadPageUrl(
  params: NativeUploadNavigationParams,
): string {
  const search = new URLSearchParams({
    session_id: params.sessionId,
    ticket: params.ticket,
    api_base_url: params.apiBaseUrl ?? resolveWxApiBaseUrl(),
  })
  return `/pages/upload/index?${search.toString()}`
}

export async function isInWeChatMiniProgram(): Promise<boolean> {
  const wxWindow = getWeChatWindow()
  if (!wxWindow) {
    return false
  }
  if (wxWindow.__wxjs_environment === "miniprogram") {
    return true
  }

  const bridge = getBridge()
  if (!bridge?.getEnv) {
    return false
  }

  return new Promise((resolve) => {
    let resolved = false
    const timer = window.setTimeout(() => {
      if (!resolved) {
        resolved = true
        resolve(false)
      }
    }, 800)

    bridge.getEnv?.((env) => {
      if (resolved) {
        return
      }
      resolved = true
      window.clearTimeout(timer)
      resolve(Boolean(env.miniprogram))
    })
  })
}

export async function navigateToNativeUploadPage(
  params: NativeUploadNavigationParams,
): Promise<NativeUploadNavigationResult> {
  const url = buildNativeUploadPageUrl(params)
  const wxWindow = getWeChatWindow()
  if (!wxWindow) {
    return { ok: false, url, reason: "not_browser" }
  }

  const inMiniProgram = await isInWeChatMiniProgram()
  if (!inMiniProgram) {
    return { ok: false, url, reason: "not_miniprogram" }
  }

  const bridge = getBridge()
  if (!bridge?.navigateTo) {
    return { ok: false, url, reason: "bridge_unavailable" }
  }

  return new Promise((resolve) => {
    bridge.navigateTo?.({
      url,
      success: () => resolve({ ok: true, url }),
      fail: () => resolve({ ok: false, url, reason: "bridge_unavailable" }),
    })
  })
}
