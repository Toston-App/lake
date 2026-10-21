import type {
  CategoryChartData,
  ChartsResponse,
  LineBarChartData,
} from '../types/chat'

export function isLineBarChartData(data: unknown): data is LineBarChartData {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false
  const d = data as LineBarChartData
  return Array.isArray(d.series) && Array.isArray(d.xAxis)
}

export function isCategoryChartData(data: unknown): data is CategoryChartData {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false
  const d = data as CategoryChartData
  return Array.isArray(d.categories)
}

export function parseChartsResponse(detail: unknown): ChartsResponse | null {
  if (!detail || typeof detail !== 'object') return null
  return detail as ChartsResponse
}

export function hasChartData(charts: ChartsResponse): boolean {
  const net = isLineBarChartData(charts.net) && charts.net.xAxis.length > 0
  const ive =
    isLineBarChartData(charts.income_vs_expense) &&
    charts.income_vs_expense.xAxis.length > 0
  const cats = isCategoryChartData(charts.categories) && charts.categories.categories.length > 0
  return net || ive || cats
}

export function formatCurrency(value: number, country?: string): string {
  const locale = country === 'AR' ? 'es-AR' : country === 'MX' ? 'es-MX' : 'en-US'
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: country === 'AR' ? 'ARS' : country === 'MX' ? 'MXN' : 'USD',
      maximumFractionDigits: 0,
    }).format(value)
  } catch {
    return value.toLocaleString()
  }
}
