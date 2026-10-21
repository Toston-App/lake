import { useState, type FormEvent } from 'react'

type LoginFormProps = {
  onLogin: (email: string, password: string) => Promise<void>
  error: string | null
  isLoading: boolean
}

export function LoginForm({ onLogin, error, isLoading }: LoginFormProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    try {
      await onLogin(email, password)
    } catch {
      // Surface via the error prop.
    }
  }

  return (
    <div className="login-panel">
      <h1>Observability</h1>
      <p className="login-subtitle">
        Superuser sign-in for usage, P&amp;L, and chat activity.
      </p>
      <form className="login-form" onSubmit={handleSubmit}>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
            disabled={isLoading}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            disabled={isLoading}
          />
        </label>
        {error && <p className="error-banner">{error}</p>}
        <button type="submit" disabled={isLoading}>
          {isLoading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
