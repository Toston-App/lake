export type TierSlug = 'free' | 'tier1' | 'tier2'

export type TierOut = {
  tier: TierSlug
  label: string
  monthly_token_limit: number | null
  features: string[]
  premium_model: boolean
  price_hint: string | null
  available: boolean
}

/** The caller's tier, as shown next to their usage. */
export type CurrentTierOut = {
  tier: TierSlug
  label: string
  premium_model: boolean
  available: boolean
}

export type UsageOut = {
  period_start: string
  period_end: string | null
  /** Share of the plan's token limit used this period, 0-100. Null when unlimited. */
  percent_used: number | null
}

export type SubscriptionOut = {
  tier: TierSlug
  status: string
  current_period_start: string | null
  current_period_end: string | null
  pending_tier: TierSlug | null
  pending_effective_at: string | null
  can_manage: boolean
}

export type SubscriptionMeOut = {
  subscription: SubscriptionOut
  usage: UsageOut
  tier_config: CurrentTierOut
}

export type CheckoutOut = {
  url: string
  checkout_id: string
}

export type PortalOut = {
  url: string
}

export type ChangePlanOut = {
  requested_tier: TierSlug
  effective: 'immediate' | 'next_period'
  effective_at: string | null
}

/** Detail payload the backend returns on a 402 quota-exceeded response. */
export type QuotaErrorDetail = {
  reason: string
  usage: UsageOut
  tier: TierSlug
  upgrade_url: string
}
