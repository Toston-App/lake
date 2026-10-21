import type { TierOut, TierSlug } from '../types/subscription'

type PricingPlansProps = {
  tiers: TierOut[]
  currentTier: TierSlug | null
  onChoose: (tier: TierSlug) => void
  busyTier: TierSlug | null
  changingPlan: boolean
}

const FEATURE_LABELS: Record<string, string> = {
  investments: 'Investment portfolio tracking',
}

function featureLabel(key: string): string {
  return FEATURE_LABELS[key] ?? key.replace(/_/g, ' ')
}

function formatUsage(tier: TierOut): string {
  if (tier.monthly_token_limit == null) return 'Unlimited usage'
  const tokens = new Intl.NumberFormat('en', { notation: 'compact' }).format(
    tier.monthly_token_limit,
  )
  return `${tokens} tokens / mo`
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
            <p className="pricing-meta">{formatUsage(tier)}</p>
            <ul className="pricing-features">
              <li>All chat features</li>
              <li>{tier.premium_model ? 'Smarter AI model' : 'Standard AI model'}</li>
              {tier.features.map((f) => (
                <li key={f}>{featureLabel(f)}</li>
              ))}
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
