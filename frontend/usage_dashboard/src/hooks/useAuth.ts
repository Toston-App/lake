import { useCallback, useState } from 'react'
import { loginAccessToken } from '../api/auth'
import { ApiError, clearToken, getToken } from '../api/client'

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => !!getToken())
  const [loginError, setLoginError] = useState<string | null>(null)
  const [isLoggingIn, setIsLoggingIn] = useState(false)

  const login = useCallback(async (email: string, password: string) => {
    setLoginError(null)
    setIsLoggingIn(true)
    try {
      await loginAccessToken(email, password)
      setIsAuthenticated(true)
    } catch (err) {
      const message =
        err instanceof ApiError ? err.detail : 'Login failed. Try again.'
      setLoginError(message)
      throw err
    } finally {
      setIsLoggingIn(false)
    }
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setIsAuthenticated(false)
    setLoginError(null)
  }, [])

  return {
    isAuthenticated,
    loginError,
    isLoggingIn,
    login,
    logout,
  }
}
