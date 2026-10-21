import { formatCurrency } from '../../utils/charts'
import { extractItems } from './extractItems'

type TransactionsListProps = {
  detail: unknown
}

type TransactionRow = {
  id?: number
  amount?: number
  date?: string
  description?: string
  type?: string
  transaction_type?: string
  account_name?: string
  category_name?: string
}

function txType(row: TransactionRow): string {
  return row.transaction_type ?? row.type ?? 'transaction'
}

export function TransactionsList({ detail }: TransactionsListProps) {
  const meta =
    detail && typeof detail === 'object'
      ? (detail as { total?: number; truncated?: boolean })
      : {}
  const items = extractItems(detail) as TransactionRow[]
  if (items.length === 0) return <p className="action-empty">No transactions found.</p>

  return (
    <div className="render-card list-card">
      <h4 className="chart-title">
        Transactions
        {typeof meta.total === 'number' ? ` (${meta.total})` : ''}
      </h4>
      <ul className="entity-list">
        {items.map((tx, i) => (
          <li key={`${tx.id ?? i}`} className="entity-row entity-row-tx">
            <div className="entity-main">
              <span className="entity-name">
                {tx.description || tx.category_name || txType(tx)}
              </span>
              <span className="entity-meta">
                {[tx.date, tx.account_name, txType(tx)].filter(Boolean).join(' · ')}
              </span>
            </div>
            <span className="entity-value">
              {formatCurrency(tx.amount ?? 0)}
            </span>
          </li>
        ))}
      </ul>
      {meta.truncated ? (
        <p className="list-truncated">Showing first results only.</p>
      ) : null}
    </div>
  )
}
