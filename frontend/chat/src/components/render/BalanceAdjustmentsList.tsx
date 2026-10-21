import { formatCurrency } from '../../utils/charts'
import { extractItems } from './extractItems'

type BalanceAdjustmentsListProps = {
  detail: unknown
}

type AdjustmentRow = {
  account_id?: number
  new_balance?: number
  adjustment_date?: string
  description?: string
}

export function BalanceAdjustmentsList({ detail }: BalanceAdjustmentsListProps) {
  const items = extractItems(detail) as AdjustmentRow[]
  if (items.length === 0) {
    return <p className="action-empty">No balance adjustments found.</p>
  }

  return (
    <div className="render-card list-card">
      <h4 className="chart-title">Balance adjustments</h4>
      <ul className="entity-list">
        {items.map((adj, i) => (
          <li key={i} className="entity-row">
            <div className="entity-main">
              <span className="entity-name">
                {adj.description || `Account ${adj.account_id ?? ''}`}
              </span>
              {adj.adjustment_date ? (
                <span className="entity-meta">{adj.adjustment_date}</span>
              ) : null}
            </div>
            <span className="entity-value">
              {formatCurrency(adj.new_balance ?? 0)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
