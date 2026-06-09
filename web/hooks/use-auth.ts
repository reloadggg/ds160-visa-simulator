import { useState, useEffect, useCallback } from "react"
import { getAuthStatus, login as apiLogin, logout as apiLogout } from "@/lib/api/client"
import {
  buildAccessKeyShareLink,
  maskAccessKeyForDisplay,
} from "@/lib/access-key-share"
import type { AccessKeyQuota } from "@/lib/api/types"

const AUTH_USER_KEY = "auth_user"
const AUTH_HISTORY_NAMESPACE_KEY = "auth_history_namespace"
const AUTH_LOGOUT_EVENT = "auth:logout"
const AUTH_CURRENT_ACCESS_KEY_STORAGE_KEY = "auth_current_access_key"
const LEGACY_HISTORY_STORAGE_KEY = "ds160-web-history-v1"
const HISTORY_STORAGE_PREFIX = "ds160-web-history-v2:"
const DEFAULT_AVATAR_URL = "/default-user-avatar.svg"

export interface AuthUserProfile {
  displayName: string
  avatarUrl: string
}

interface StoredCurrentAccessKey {
  keyId: string
  key: string
}

function generateDefaultUserName(): string {
  const value = Math.floor(100000 + Math.random() * 900000)
  return `User_${value}`
}

function normalizeDisplayName(displayName?: string): string {
  const trimmed = displayName?.trim()
  return trimmed || generateDefaultUserName()
}

function buildUserProfile(displayName?: string): AuthUserProfile {
  return {
    displayName: normalizeDisplayName(displayName),
    avatarUrl: DEFAULT_AVATAR_URL,
  }
}

function readStoredUserProfile(): AuthUserProfile | null {
  if (typeof window === "undefined") {
    return null
  }

  try {
    const raw = localStorage.getItem(AUTH_USER_KEY)
    if (!raw) {
      return null
    }
    const parsed = JSON.parse(raw) as Partial<AuthUserProfile>
    if (typeof parsed.displayName !== "string" || !parsed.displayName.trim()) {
      return null
    }
    return {
      displayName: parsed.displayName.trim(),
      avatarUrl:
        typeof parsed.avatarUrl === "string" && parsed.avatarUrl.trim()
          ? parsed.avatarUrl
          : DEFAULT_AVATAR_URL,
    }
  } catch {
    return null
  }
}

function writeStoredUserProfile(profile: AuthUserProfile): void {
  if (typeof window === "undefined") {
    return
  }
  localStorage.setItem(AUTH_USER_KEY, JSON.stringify(profile))
}

function clearStoredUserProfile(): void {
  if (typeof window === "undefined") {
    return
  }
  localStorage.removeItem(AUTH_USER_KEY)
}

function readStoredCurrentAccessKey(expectedKeyId?: string | null): string | null {
  if (typeof window === "undefined") {
    return null
  }

  try {
    const raw = window.sessionStorage.getItem(AUTH_CURRENT_ACCESS_KEY_STORAGE_KEY)
    if (!raw) {
      return null
    }
    const parsed = JSON.parse(raw) as Partial<StoredCurrentAccessKey>
    const key = typeof parsed.key === "string" ? parsed.key.trim() : ""
    const keyId = typeof parsed.keyId === "string" ? parsed.keyId.trim() : ""
    if (!key || !keyId || (expectedKeyId && keyId !== expectedKeyId)) {
      clearStoredCurrentAccessKey()
      return null
    }
    return key
  } catch {
    clearStoredCurrentAccessKey()
    return null
  }
}

function writeStoredCurrentAccessKey(keyId: string, key: string): void {
  if (typeof window === "undefined") {
    return
  }
  const trimmedKey = key.trim()
  const trimmedKeyId = keyId.trim()
  if (!trimmedKey || !trimmedKeyId) {
    clearStoredCurrentAccessKey()
    return
  }
  try {
    window.sessionStorage.setItem(
      AUTH_CURRENT_ACCESS_KEY_STORAGE_KEY,
      JSON.stringify({ keyId: trimmedKeyId, key: trimmedKey }),
    )
  } catch {
    // 当前 Key 分享链接只是客户侧便捷能力；sessionStorage 不可用时不能影响登录主流程。
  }
}

function clearStoredCurrentAccessKey(): void {
  if (typeof window === "undefined") {
    return
  }
  try {
    window.sessionStorage.removeItem(AUTH_CURRENT_ACCESS_KEY_STORAGE_KEY)
  } catch {
    // Ignore storage cleanup failures; auth cookie cleanup remains the source of truth.
  }
}

function syncHistoryNamespace(nextNamespace?: string | null): void {
  if (typeof window === "undefined") {
    return
  }
  const normalized = nextNamespace?.trim() || "local-dev"
  const previous = localStorage.getItem(AUTH_HISTORY_NAMESPACE_KEY)
  const legacyHistory = localStorage.getItem(LEGACY_HISTORY_STORAGE_KEY)
  if ((previous && previous !== normalized) || (!previous && normalized !== "local-dev" && legacyHistory)) {
    localStorage.removeItem(LEGACY_HISTORY_STORAGE_KEY)
  }
  localStorage.setItem(AUTH_HISTORY_NAMESPACE_KEY, normalized)
}

function clearHistoryNamespace(): void {
  if (typeof window === "undefined") {
    return
  }
  const namespace = localStorage.getItem(AUTH_HISTORY_NAMESPACE_KEY)
  if (namespace) {
    localStorage.removeItem(`${HISTORY_STORAGE_PREFIX}${namespace}`)
  }
  localStorage.removeItem(AUTH_HISTORY_NAMESPACE_KEY)
  localStorage.removeItem(LEGACY_HISTORY_STORAGE_KEY)
}

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [isCheckingAuth, setIsCheckingAuth] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [userProfile, setUserProfile] = useState<AuthUserProfile | null>(null)
  const [accessKeyQuota, setAccessKeyQuota] = useState<AccessKeyQuota | null>(null)
  const [currentAccessKeyShareLink, setCurrentAccessKeyShareLink] =
    useState<string | null>(null)
  const [maskedCurrentAccessKey, setMaskedCurrentAccessKey] =
    useState<string | null>(null)

  const applyCurrentAccessKey = useCallback(
    (accessKey: string | null, quota?: AccessKeyQuota | null) => {
      const trimmedKey = accessKey?.trim()
      if (!trimmedKey || !quota?.key_id) {
        clearStoredCurrentAccessKey()
        setCurrentAccessKeyShareLink(null)
        setMaskedCurrentAccessKey(null)
        return
      }
      writeStoredCurrentAccessKey(quota.key_id, trimmedKey)
      setCurrentAccessKeyShareLink(buildAccessKeyShareLink(trimmedKey))
      setMaskedCurrentAccessKey(maskAccessKeyForDisplay(trimmedKey))
    },
    [],
  )

  const clearCurrentAccessKey = useCallback(() => {
    clearStoredCurrentAccessKey()
    setCurrentAccessKeyShareLink(null)
    setMaskedCurrentAccessKey(null)
  }, [])

  useEffect(() => {
    let cancelled = false

    const clearAuthState = (message?: string) => {
      clearStoredUserProfile()
      clearHistoryNamespace()
      setUserProfile(null)
      setAccessKeyQuota(null)
      clearCurrentAccessKey()
      setIsAuthenticated(false)
      setError(message ?? null)
    }

    const restoreAuthStatus = async () => {
      try {
        const status = await getAuthStatus()
        if (cancelled) {
          return
        }
        if (!status.authenticated) {
          clearAuthState()
          return
        }
        syncHistoryNamespace(status.history_namespace)
        const profile = readStoredUserProfile() ?? buildUserProfile()
        writeStoredUserProfile(profile)
        setUserProfile(profile)
        const quota = status.access_key_quota ?? null
        const storedCurrentKey = quota
          ? readStoredCurrentAccessKey(quota.key_id)
          : null
        if (!quota) {
          clearStoredCurrentAccessKey()
        }
        setAccessKeyQuota(quota)
        applyCurrentAccessKey(storedCurrentKey, quota)
        setIsAuthenticated(true)
      } catch {
        if (!cancelled) {
          clearAuthState()
        }
      } finally {
        if (!cancelled) {
          setIsCheckingAuth(false)
        }
      }
    }

    const handleUnauthorized = () => {
      clearAuthState("会话已过期，请重新登录")
    }

    const handleLogout = () => {
      clearAuthState()
    }

    void restoreAuthStatus()
    window.addEventListener("auth:unauthorized", handleUnauthorized)
    window.addEventListener(AUTH_LOGOUT_EVENT, handleLogout)
    return () => {
      cancelled = true
      window.removeEventListener("auth:unauthorized", handleUnauthorized)
      window.removeEventListener(AUTH_LOGOUT_EVENT, handleLogout)
    }
  }, [applyCurrentAccessKey, clearCurrentAccessKey])

  const updateUserProfile = useCallback((displayName: string) => {
    const profile = buildUserProfile(displayName)
    writeStoredUserProfile(profile)
    setUserProfile(profile)
  }, [])

  const login = useCallback(async (password: string, displayName?: string) => {
    setIsLoggingIn(true)
    setError(null)
    try {
      const response = await apiLogin(password)
      syncHistoryNamespace(response.history_namespace)
      const storedProfile = readStoredUserProfile()
      const profile = displayName?.trim()
        ? buildUserProfile(displayName)
        : (storedProfile ?? buildUserProfile())
      writeStoredUserProfile(profile)
      setUserProfile(profile)
      const quota = response.access_key_quota ?? null
      setAccessKeyQuota(quota)
      applyCurrentAccessKey(password, quota)
      setIsAuthenticated(true)
      return true
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "登录失败"
      setError(errorMessage)
      return false
    } finally {
      setIsLoggingIn(false)
    }
  }, [applyCurrentAccessKey])

  const logout = useCallback(async () => {
    try {
      await apiLogout()
    } finally {
      window.dispatchEvent(new CustomEvent(AUTH_LOGOUT_EVENT))
    }
  }, [])

  return {
    isAuthenticated,
    isCheckingAuth,
    isLoggingIn,
    error,
    userProfile,
    accessKeyQuota,
    currentAccessKeyShareLink,
    maskedCurrentAccessKey,
    login,
    logout,
    updateUserProfile,
  }
}
