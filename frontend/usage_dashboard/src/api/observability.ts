import { apiFetch } from './client'
import type {
  ModelStat,
  ObservabilityRangeKey,
  ObservabilitySummary,
  TimeseriesPoint,
  ToolStat,
  TurnDetail,
  TurnsListResponse,
} from '../types/observability'

const ADMIN_PREFIX = '/api/v1/admin/observability'

function rangeToParams(range: ObservabilityRangeKey): string {
  if (range === 'all') return ''
  const days = range === '7d' ? 7 : 30
  const start = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
  return `start=${encodeURIComponent(start)}`
}

function withRange(range: ObservabilityRangeKey): string {
  const r = rangeToParams(range)
  return r ? `?${r}` : ''
}

export function fetchSummary(
  range: ObservabilityRangeKey,
): Promise<ObservabilitySummary> {
  return apiFetch<ObservabilitySummary>(
    `${ADMIN_PREFIX}/summary${withRange(range)}`,
  )
}

export function fetchCostTimeseries(
  range: ObservabilityRangeKey,
  bucket: 'hour' | 'day' | 'week' | 'month' = 'day',
): Promise<TimeseriesPoint[]> {
  const r = rangeToParams(range)
  const q = [r, `bucket=${bucket}`].filter(Boolean).join('&')
  return apiFetch<TimeseriesPoint[]>(`${ADMIN_PREFIX}/cost-timeseries?${q}`)
}

export function fetchToolStats(range: ObservabilityRangeKey): Promise<ToolStat[]> {
  return apiFetch<ToolStat[]>(`${ADMIN_PREFIX}/tools${withRange(range)}`)
}

export function fetchModelStats(range: ObservabilityRangeKey): Promise<ModelStat[]> {
  return apiFetch<ModelStat[]>(`${ADMIN_PREFIX}/models${withRange(range)}`)
}

export type TurnsFilter = {
  range: ObservabilityRangeKey
  status?: string
  search?: string
  model?: string
  skip?: number
  limit?: number
}

export function fetchTurns(filter: TurnsFilter): Promise<TurnsListResponse> {
  const parts: string[] = []
  const r = rangeToParams(filter.range)
  if (r) parts.push(r)
  if (filter.status) parts.push(`status=${encodeURIComponent(filter.status)}`)
  if (filter.search) parts.push(`search=${encodeURIComponent(filter.search)}`)
  if (filter.model) parts.push(`model=${encodeURIComponent(filter.model)}`)
  parts.push(`skip=${filter.skip ?? 0}`)
  parts.push(`limit=${filter.limit ?? 50}`)
  return apiFetch<TurnsListResponse>(`${ADMIN_PREFIX}/turns?${parts.join('&')}`)
}

export function fetchTurnDetail(turnId: number): Promise<TurnDetail> {
  return apiFetch<TurnDetail>(`${ADMIN_PREFIX}/turns/${turnId}`)
}
