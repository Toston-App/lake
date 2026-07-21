import { useState } from 'react'
import type { SubscriptionMeOut, TierSlug } from '../types/subscription'
import { PricingPlans } from './PricingPlans'

type AccountViewProps = {
  data: SubscriptionMeOut | null
  tiers: SubscriptionMeOut['tier_config'][]
  loading: boolean
  onChooseTier: (tier: TierSlug) => Promise<void>
  onManage: () => Promise<void>
  onBack: () => void
}

function formatPeriod(start: string | null, end: string | null): string {
  if (!start) return '—'
  const fmt = (iso: string) => new Date(iso).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
  return end ? `${fmt(start)} → ${fmt(end)}` : `since ${fmt(start)}`
}

export function AccountView({
  data,
  tiers,
  loading,
  onChooseTier,
  onManage,
  onBack,
}: AccountViewProps) {
  const [busyTier, setBusyTier] = useState<TierSlug | null>(null)
  const [managing, setManaging] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleChoose(tier: TierSlug) {
    setError(null)
    setBusyTier(tier)
    try {
      await onChooseTier(tier)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start checkout')
    } finally {
      setBusyTier(null)
    }
  }

  async function handleManage() {
    setError(null)
    setManaging(true)
    try {
      await onManage()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to open portal')
    } finally {
      setManaging(false)
    }
  }

  const sub = data?.subscription
  const usage = data?.usage
  const isActive = sub?.status === 'active' || sub?.status === 'trialing'

  return (
    <div className="app app-account">
      <header className="chat-header">
        <h1>Account &amp; Plans</h1>
        <div className="chat-header-actions">
          <button type="button" className="logout-btn" onClick={onBack}>
            Back to chat
          </button>
        </div>
      </header>

      <main className="account-main">
        {loading && <p className="muted">Loading subscription…</p>}
        {error && <p className="error-banner">{error}</p>}

        {sub && (
          <section className="account-current">
            <h2>Current plan</h2>
            <div className="account-current-card">
              <div>
                <span className="account-tier-name">{sub.tier}</span>
                <span className="account-tier-status" data-status={sub.status}>
                  {sub.status}
                </span>
              </div>
              {isActive && (
                <span className="account-period">
                  {formatPeriod(sub.current_period_start, sub.current_period_end)}
                </span>
              )}
              {usage && (
                <div className="account-usage">
                  <span>
                    Messages: <strong>{usage.messages_used}</strong> /{' '}
                    {usage.messages_limit ?? '∞'}
                  </span>
                  <span>
                    Tokens: <strong>{usage.tokens_used.toLocaleString()}</strong> /{' '}
                    {usage.tokens_limit?.toLocaleString() ?? '∞'}
                  </span>
                </div>
              )}
              {isActive && sub.can_manage && (
                <button
                  type="button"
                  className="logout-btn"
                  disabled={managing}
                  onClick={handleManage}
                >
                  {managing ? 'Opening…' : 'Manage subscription'}
                </button>
              )}
            </div>
          </section>
        )}

        <section className="account-plans">
          <h2>Plans</h2>
          <PricingPlans
            tiers={tiers}
            currentTier={sub?.tier ?? null}
            onChoose={handleChoose}
            busyTier={busyTier}
          />
        </section>
      </main>
    </div>
  )
}
