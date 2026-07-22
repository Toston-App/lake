import type { TierOut, TierSlug } from '../types/subscription'

type PricingPlansProps = {
  tiers: TierOut[]
  currentTier: TierSlug | null
  onChoose: (tier: TierSlug) => void
  busyTier: TierSlug | null
  changingPlan: boolean
}

const FEATURE_LABELS: Record<string, string> = {
  search_web: 'Web search',
  charts: 'Charts & visualizations',
  advanced_analytics: 'Advanced analytics',
  bulk_operations: 'Bulk operations',
  priority_support: 'Priority support',
}

function featureLabel(key: string): string {
  return FEATURE_LABELS[key] ?? key.replace(/_/g, ' ')
}

function formatMessages(limit: number | null): string {
  if (limit == null) return 'Unlimited messages'
  return `${limit} messages / mo`
}

export function PricingPlans({
  tiers,
  currentTier,
  onChoose,
  busyTier,
  changingPlan,
}: PricingPlansProps) {
  const payingTiers = tiers.filter((t) => t.tier !== 'free')

  return (
    <div className="pricing-plans">
      {payingTiers.map((tier) => {
        const isCurrent = currentTier === tier.tier
        const isBusy = busyTier === tier.tier
        const disabled = isCurrent || isBusy || !tier.available
        return (
          <div
            key={tier.tier}
            className={`pricing-card ${isCurrent ? 'is-current' : ''}`}
          >
            <div className="pricing-card-header">
              <h3>{tier.label}</h3>
              <span className="pricing-price">
                {tier.price_hint ?? '—'}
              </span>
            </div>
            <p className="pricing-meta">{formatMessages(tier.monthly_message_limit)}</p>
            <ul className="pricing-features">
              {tier.features.map((f) => (
                <li key={f}>{featureLabel(f)}</li>
              ))}
              {tier.features.length === 0 && (
                <li className="muted">Basic chat tools</li>
              )}
            </ul>
            <button
              type="button"
              className="pricing-choose-btn"
              disabled={disabled}
              onClick={() => onChoose(tier.tier)}
            >
              {isCurrent
                ? 'Current plan'
                : isBusy
                  ? 'Redirecting…'
                  : !tier.available
                    ? 'Unavailable'
                    : changingPlan
                      ? 'Change plan'
                      : 'Choose'}
            </button>
          </div>
        )
      })}
    </div>
  )
}
