import type { SummaryResponse } from '../../types/chat'
import { formatCurrency } from '../../utils/charts'

type SummaryCardProps = {
  detail: unknown
}

function asSummary(detail: unknown): SummaryResponse | null {
  if (!detail || typeof detail !== 'object') return null
  const d = detail as SummaryResponse
  if (typeof d.period_net !== 'number' || !d.balance) return null
  return d
}

export function SummaryCard({ detail }: SummaryCardProps) {
  const summary = asSummary(detail)
  if (!summary) return null

  const fmt = (n: number) => formatCurrency(n, summary.country)

  return (
    <div className="summary-card">
      <h4 className="chart-title">Period summary</h4>
      <div className="summary-grid">
        <div className="summary-stat">
          <span className="summary-label">Income</span>
          <span className="summary-value summary-positive">
            {fmt(summary.period_income)}
          </span>
        </div>
        <div className="summary-stat">
          <span className="summary-label">Expenses</span>
          <span className="summary-value summary-negative">
            {fmt(summary.period_expenses)}
          </span>
        </div>
        <div className="summary-stat summary-stat-highlight">
          <span className="summary-label">Net</span>
          <span
            className={
              summary.period_net >= 0
                ? 'summary-value summary-positive'
                : 'summary-value summary-negative'
            }
          >
            {fmt(summary.period_net)}
          </span>
        </div>
        <div className="summary-stat">
          <span className="summary-label">Total balance</span>
          <span className="summary-value">{fmt(summary.balance.total)}</span>
        </div>
      </div>
    </div>
  )
}
