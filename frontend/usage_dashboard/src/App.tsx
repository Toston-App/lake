import { useEffect, useState } from 'react'
import { probeAdminAccess } from './api/auth'
import { ApiError } from './api/client'
import { Forbidden } from './components/Forbidden'
import { LoginForm } from './components/LoginForm'
import { Shell } from './components/Shell'
import { useAuth } from './hooks/useAuth'

function App() {
  const { isAuthenticated, loginError, isLoggingIn, login, logout } = useAuth()
  const [canAccess, setCanAccess] = useState<boolean | null>(null)
  const [probeError, setProbeError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const task = window.setTimeout(() => {
      if (!isAuthenticated) {
        if (!cancelled) {
          setCanAccess(null)
          setProbeError(null)
        }
        return
      }
      setCanAccess(null)
      setProbeError(null)
      probeAdminAccess()
        .then((ok) => {
          if (!cancelled) setCanAccess(ok)
        })
        .catch((err) => {
          if (cancelled) return
          if (err instanceof ApiError && err.status === 401) {
            logout()
            return
          }
          setProbeError(
            err instanceof ApiError ? err.detail : 'Failed to check access',
          )
        })
    }, 0)
    return () => {
      cancelled = true
      window.clearTimeout(task)
    }
  }, [isAuthenticated, logout])

  if (!isAuthenticated) {
    return (
      <div className="app app-login">
        <LoginForm
          onLogin={login}
          error={loginError}
          isLoading={isLoggingIn}
        />
      </div>
    )
  }

  if (probeError) {
    return (
      <div className="app app-login">
        <div className="login-panel">
          <p className="error-banner">{probeError}</p>
          <button type="button" className="logout-btn" onClick={logout}>
            Log out
          </button>
        </div>
      </div>
    )
  }

  if (canAccess === null) {
    return (
      <div className="app app-login">
        <p className="admin-loading">Loading…</p>
      </div>
    )
  }

  if (!canAccess) {
    return <Forbidden onLogout={logout} />
  }

  return <Shell onLogout={logout} />
}

export default App
