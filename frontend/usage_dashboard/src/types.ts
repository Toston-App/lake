export type TokenResponse = {
  access_token: string
  token_type: string
}

export type TimeRangeKey = '7d' | '30d' | 'mtd'

export type OutcomeFilter = 'all' | 'winning' | 'losing'

export type SortKey = 'profit' | 'cost' | 'revenue' | 'tokens' | 'turns'

export type EconomicsTierBreakdown = {
  tier: string
  label: string
  users: number
  ai_cost_usd: number
  estimated_revenue_usd: number
  profit_usd: number
}

export type EconomicsSummary = {
  window_days: number
  users: number
  paying_users: number
  losing_users: number
  winning_users: number
  ai_cost_usd: number
  estimated_revenue_usd: number
  profit_usd: number
  by_tier: EconomicsTierBreakdown[]
}

export type EconomicsUserRow = {
  user_id: number
  email: string | null
  name: string | null
  tier: string
  tier_label: string
  status: string
  is_paid: boolean
  turns: number
  tokens: number
  tokens_limit: number | null
  ai_cost_usd: number
  monthly_price_usd: number
  estimated_revenue_usd: number
  profit_usd: number
}

export type EconomicsUsersResponse = {
  items: EconomicsUserRow[]
  count: number
}
