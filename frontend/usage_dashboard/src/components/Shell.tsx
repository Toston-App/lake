import { useState } from 'react'
import { ActivityPanel } from './ActivityPanel'
import { Dashboard } from './Dashboard'

type Tab = 'pnl' | 'activity'

type Props = {
  onLogout: () => void
}

export function Shell({ onLogout }: Props) {
  const [tab, setTab] = useState<Tab>('pnl')

  return (
    <div className="app admin-view">
      <header className="chat-header">
        <h1>Observability</h1>
        <div className="admin-header-actions">
          <nav className="tab-toggle" aria-label="Observability sections">
            <button
              type="button"
              className={tab === 'pnl' ? 'range-btn active' : 'range-btn'}
              onClick={() => setTab('pnl')}
            >
              Usage &amp; P&amp;L
            </button>
            <button
              type="button"
              className={tab === 'activity' ? 'range-btn active' : 'range-btn'}
              onClick={() => setTab('activity')}
            >
              Activity
            </button>
          </nav>
          <button type="button" className="logout-btn" onClick={onLogout}>
            Log out
          </button>
        </div>
      </header>
      {tab === 'pnl' ? <Dashboard /> : <ActivityPanel />}
    </div>
  )
}
