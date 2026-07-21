import { formatCurrency } from '../../utils/charts'
import { extractItems } from './extractItems'

type AccountsListProps = {
  detail: unknown
}

type AccountRow = {
  name?: string
  type?: string
  current_balance?: number
}

export function AccountsList({ detail }: AccountsListProps) {
  const items = extractItems(detail) as AccountRow[]
  if (items.length === 0) return <p className="action-empty">No accounts found.</p>

  return (
    <div className="render-card list-card">
      <h4 className="chart-title">Accounts</h4>
      <ul className="entity-list">
        {items.map((account, i) => (
          <li key={`${account.name ?? 'account'}-${i}`} className="entity-row">
            <div className="entity-main">
              <span className="entity-name">{account.name ?? 'Account'}</span>
              {account.type ? (
                <span className="entity-meta">{account.type}</span>
              ) : null}
            </div>
            <span className="entity-value">
              {formatCurrency(account.current_balance ?? 0)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
