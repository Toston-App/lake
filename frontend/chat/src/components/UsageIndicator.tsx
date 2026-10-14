import type { SubscriptionMeOut } from '../types/subscription'

type UsageIndicatorProps = {
  data: SubscriptionMeOut | null
  onUpgrade: () => void
}

export function UsageIndicator({ data, onUpgrade }: UsageIndicatorProps) {
  if (!data) return null

  const { usage, subscription } = data
  const isFree = subscription.tier === 'free'
  const pct = usage.percent_used
  const over = pct != null && pct >= 100

  return (
    <div className={`usage-indicator ${over ? 'is-over' : ''}`}>
      <span className="usage-tier-badge" data-tier={subscription.tier}>
        {subscription.tier}
      </span>
      <span className="usage-text">
        ⚡ {pct == null ? 'Unlimited' : `${Math.floor(pct)}% used`}
      </span>
      {pct != null && (
        <span className="usage-bar">
          <span className="usage-bar-fill" style={{ width: `${pct}%` }} />
        </span>
      )}
      {isFree && (
        <button type="button" className="usage-upgrade-btn" onClick={onUpgrade}>
          Upgrade
        </button>
      )}
    </div>
  )
}
