import type { SubscriptionMeOut } from '../types/subscription'

type UsageIndicatorProps = {
  data: SubscriptionMeOut | null
  onUpgrade: () => void
}

function formatLimit(limit: number | null): string {
  return limit == null ? '∞' : String(limit)
}

export function UsageIndicator({ data, onUpgrade }: UsageIndicatorProps) {
  if (!data) return null

  const { usage, subscription } = data
  const isFree = subscription.tier === 'free'
  const limit = usage.messages_limit
  const used = usage.messages_used
  const over = limit != null && used >= limit
  const pct = limit != null ? Math.min(100, Math.round((used / limit) * 100)) : 0

  return (
    <div className={`usage-indicator ${over ? 'is-over' : ''}`}>
      <span className="usage-tier-badge" data-tier={subscription.tier}>
        {subscription.tier}
      </span>
      <span className="usage-text">
        ⚡ {used}/{formatLimit(limit)} messages
      </span>
      {limit != null && (
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
