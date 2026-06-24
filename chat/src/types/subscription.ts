export type TierSlug = 'free' | 'tier1' | 'tier2' | 'tier3'

export type TierOut = {
  tier: TierSlug
  label: string
  monthly_message_limit: number | null
  monthly_token_limit: number | null
  features: string[]
  polar_product_id: string | null
  price_hint: string | null
  available: boolean
}

export type UsageOut = {
  period_start: string
  period_end: string | null
  messages_used: number
  messages_limit: number | null
  tokens_used: number
  tokens_limit: number | null
  remaining: number | null
}

export type SubscriptionOut = {
  tier: TierSlug
  status: string
  current_period_start: string | null
  current_period_end: string | null
  polar_customer_id: string | null
  manage_url: string | null
}

export type SubscriptionMeOut = {
  subscription: SubscriptionOut
  usage: UsageOut
  tier_config: TierOut
}

export type CheckoutOut = {
  url: string
  checkout_id: string
}

export type PortalOut = {
  url: string
}

/** Detail payload the backend returns on a 402 quota-exceeded response. */
export type QuotaErrorDetail = {
  reason: string
  usage: UsageOut
  tier: TierSlug
  upgrade_url: string
}
