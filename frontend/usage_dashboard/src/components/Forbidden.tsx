type ForbiddenProps = {
  onLogout: () => void
}

export function Forbidden({ onLogout }: ForbiddenProps) {
  return (
    <div className="app app-login">
      <div className="login-panel">
        <h1>No access</h1>
        <p className="login-subtitle">
          This account cannot open the observability dashboard.
        </p>
        <button type="button" className="logout-btn" onClick={onLogout}>
          Log out
        </button>
      </div>
    </div>
  )
}
