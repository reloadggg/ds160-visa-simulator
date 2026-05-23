import { useState, useEffect, useCallback } from "react"
import { getAuthStatus, login as apiLogin, logout as apiLogout } from "@/lib/api/client"

const AUTH_USER_KEY = "auth_user"
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

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [isCheckingAuth, setIsCheckingAuth] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [userProfile, setUserProfile] = useState<AuthUserProfile | null>(null)

  useEffect(() => {
    let cancelled = false

    const restoreAuthStatus = async () => {
      try {
        const status = await getAuthStatus()
        if (cancelled) {
          return
        }
        if (!status.authenticated) {
          clearStoredUserProfile()
          setUserProfile(null)
          setIsAuthenticated(false)
          return
        }
        const profile = readStoredUserProfile() ?? buildUserProfile()
        writeStoredUserProfile(profile)
        setUserProfile(profile)
        setIsAuthenticated(true)
      } catch {
        if (!cancelled) {
          clearStoredUserProfile()
          setUserProfile(null)
          setIsAuthenticated(false)
        }
      } finally {
        if (!cancelled) {
          setIsCheckingAuth(false)
        }
      }
    }

    const handleUnauthorized = () => {
      clearStoredUserProfile()
      setUserProfile(null)
      setIsAuthenticated(false)
      setError("会话已过期，请重新登录")
    }

    void restoreAuthStatus()
    window.addEventListener("auth:unauthorized", handleUnauthorized)
    return () => {
      cancelled = true
      window.removeEventListener("auth:unauthorized", handleUnauthorized)
    }
  }, [])

  const login = useCallback(async (password: string, displayName?: string) => {
    setIsLoggingIn(true)
    setError(null)
    try {
      await apiLogin(password)
      const profile = buildUserProfile(displayName)
      writeStoredUserProfile(profile)
      setUserProfile(profile)
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
    await apiLogout()
    clearStoredUserProfile()
    setUserProfile(null)
    setIsAuthenticated(false)
  }, [])

  return {
    isAuthenticated,
    isCheckingAuth,
    isLoggingIn,
    error,
    userProfile,
    login,
    logout,
  }
}
