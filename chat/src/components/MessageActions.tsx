import type { ChatAction } from '../types/chat'
import { ChartsPanel } from './charts/ChartsPanel'
import { SummaryCard } from './charts/SummaryCard'

type MessageActionsProps = {
  actions: ChatAction[]
}

const ACTION_LABELS: Record<string, string> = {
  expense_created: 'Expense created',
  income_created: 'Income created',
  expense_updated: 'Expense updated',
  income_updated: 'Income updated',
  expense_deleted: 'Expense deleted',
  income_deleted: 'Income deleted',
  transfer_created: 'Transfer created',
  accounts_listed: 'Accounts loaded',
  categories_listed: 'Categories loaded',
  profile_fetched: 'Profile loaded',
}

function GenericActionCard({ action }: { action: ChatAction }) {
  const label = ACTION_LABELS[action.type] ?? action.type.replace(/_/g, ' ')
  const hasDetail = action.detail && Object.keys(action.detail).length > 0

  return (
    <details className="action-card action-card-generic">
      <summary>{label}</summary>
      {hasDetail && (
        <pre className="action-raw">{JSON.stringify(action.detail, null, 2)}</pre>
      )}
    </details>
  )
}

export function MessageActions({ actions }: MessageActionsProps) {
  if (actions.length === 0) return null

  return (
    <div className="message-actions">
      {actions.map((action, i) => {
        if (action.type === 'summary_fetched') {
          return (
            <SummaryCard key={`${action.type}-${i}`} detail={action.detail} />
          )
        }
        if (action.type === 'charts_fetched') {
          return (
            <ChartsPanel key={`${action.type}-${i}`} detail={action.detail} />
          )
        }
        return <GenericActionCard key={`${action.type}-${i}`} action={action} />
      })}
    </div>
  )
}
