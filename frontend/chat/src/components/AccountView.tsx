import { useState } from 'react'
import type {
  ChangePlanOut,
  SubscriptionMeOut,
  TierOut,
  TierSlug,
} from '../types/subscription'
import { PricingPlans } from './PricingPlans'

type AccountViewProps = {
  data: SubscriptionMeOut | null
  tiers: TierOut[]
  loading: boolean
  onChooseTier: (tier: TierSlug) => Promise<ChangePlanOut | void>
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
  const [planChange, setPlanChange] = useState<ChangePlanOut | null>(null)

  async function handleChoose(tier: TierSlug) {
    setError(null)
    setBusyTier(tier)
    try {
      const result = await onChooseTier(tier)
      if (result) setPlanChange(result)
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
  const pendingTier = sub?.pending_tier ?? (
    planChange?.effective === 'next_period' ? planChange.requested_tier : null
  )
  const pendingAt = sub?.pending_effective_at ?? planChange?.effective_at ?? null

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
                    Usage:{' '}
                    <strong>
                      {usage.percent_used == null
                        ? 'Unlimited'
                        : `${Math.floor(usage.percent_used)}%`}
                    </strong>{' '}
                    of this period&apos;s plan
                  </span>
                </div>
              )}
              {planChange?.effective === 'immediate' && (
                <p className="muted">
                  Upgrading to {planChange.requested_tier}; awaiting billing confirmation…
                </p>
              )}
              {pendingTier && (
                <p className="muted">
                  Scheduled change to {pendingTier}
                  {pendingAt ? ` on ${new Date(pendingAt).toLocaleDateString()}` : ''}.
                </p>
              )}
              {sub.can_manage && (
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
            changingPlan={sub?.status === 'active'}
          />
        </section>
      </main>
    </div>
  )
}
