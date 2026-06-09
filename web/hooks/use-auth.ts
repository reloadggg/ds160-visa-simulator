import { useState, useEffect, useCallback } from "react"
import { getAuthStatus, login as apiLogin, logout as apiLogout } from "@/lib/api/client"
import type { AccessKeyQuota } from "@/lib/api/types"

const AUTH_USER_KEY = "auth_user"
const AUTH_HISTORY_NAMESPACE_KEY = "auth_history_namespace"
const AUTH_LOGOUT_EVENT = "auth:logout"
const LEGACY_HISTORY_STORAGE_KEY = "ds160-web-history-v1"
const HISTORY_STORAGE_PREFIX = "ds160-web-history-v2:"
const DEFAULT_AVATAR_URL = "/default-user-avatar.svg"

export interface AuthUserProfile {
  displayName: string
  avatarUrl: string
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

  useEffect(() => {
    let cancelled = false

    const clearAuthState = (message?: string) => {
      clearStoredUserProfile()
      clearHistoryNamespace()
      setUserProfile(null)
      setAccessKeyQuota(null)
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
        setAccessKeyQuota(status.access_key_quota ?? null)
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
  }, [])

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
      setAccessKeyQuota(response.access_key_quota ?? null)
      setIsAuthenticated(true)
      return true
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "登录失败"
      setError(errorMessage)
      return false
    } finally {
      setIsLoggingIn(false)
    }
  }, [])

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
    login,
    logout,
    updateUserProfile,
  }
}
