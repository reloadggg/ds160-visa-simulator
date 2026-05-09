import { useState, useEffect, useCallback } from "react"
import { login as apiLogin, logout as apiLogout } from "@/lib/api/client"

const AUTH_TOKEN_KEY = "auth_token"

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    if (token) {
      queueMicrotask(() => setIsAuthenticated(true))
    }

    // Listen for unauthorized event from API client
    const handleUnauthorized = () => {
      setIsAuthenticated(false)
      setError("会话已过期，请重新登录")
    }

    window.addEventListener("auth:unauthorized", handleUnauthorized)
    return () => {
      window.removeEventListener("auth:unauthorized", handleUnauthorized)
    }
  }, [])

  const login = useCallback(async (password: string) => {
    setIsLoggingIn(true)
    setError(null)
    try {
      await apiLogin(password)
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

  const logout = useCallback(() => {
    apiLogout()
    setIsAuthenticated(false)
  }, [])

  return {
    isAuthenticated,
    isLoggingIn,
    error,
    login,
    logout,
  }
}
