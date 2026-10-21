import { apiFetch } from './client'
import type {
  EconomicsSummary,
  EconomicsUsersResponse,
  OutcomeFilter,
  SortKey,
  TimeRangeKey,
} from '../types'

const ADMIN_PREFIX = '/api/v1/admin/observability'

export function rangeBounds(range: TimeRangeKey): { start: string; end: string } {
  const end = new Date()
  const start = new Date(end)
  if (range === '7d') {
    start.setTime(end.getTime() - 7 * 24 * 60 * 60 * 1000)
  } else if (range === '30d') {
    start.setTime(end.getTime() - 30 * 24 * 60 * 60 * 1000)
  } else {
    start.setUTCDate(1)
    start.setUTCHours(0, 0, 0, 0)
  }
  return { start: start.toISOString(), end: end.toISOString() }
}

function withBounds(range: TimeRangeKey, extra: Record<string, string | number> = {}) {
  const { start, end } = rangeBounds(range)
  const params = new URLSearchParams({ start, end })
  for (const [key, value] of Object.entries(extra)) {
    params.set(key, String(value))
  }
  return params.toString()
}

export function fetchEconomics(range: TimeRangeKey): Promise<EconomicsSummary> {
  return apiFetch<EconomicsSummary>(`${ADMIN_PREFIX}/economics?${withBounds(range)}`)
}

export type UsersQuery = {
  range: TimeRangeKey
  tier?: string
  outcome?: OutcomeFilter
  search?: string
  sort?: SortKey
  order?: 'asc' | 'desc'
  skip?: number
  limit?: number
}

export function fetchEconomicsUsers(query: UsersQuery): Promise<EconomicsUsersResponse> {
  const extra: Record<string, string | number> = {
    skip: query.skip ?? 0,
    limit: query.limit ?? 50,
    sort: query.sort ?? 'profit',
    order: query.order ?? 'asc',
    outcome: query.outcome ?? 'all',
  }
  if (query.tier) extra.tier = query.tier
  if (query.search) extra.search = query.search
  return apiFetch<EconomicsUsersResponse>(
    `${ADMIN_PREFIX}/users?${withBounds(query.range, extra)}`,
  )
}
