import { ChartsPanel } from '../charts/ChartsPanel'
import { SummaryCard } from '../charts/SummaryCard'
import { AccountsList } from './AccountsList'
import { BalanceAdjustmentsList } from './BalanceAdjustmentsList'
import { CategoriesList } from './CategoriesList'
import { DataTable } from './DataTable'
import { PlacesList } from './PlacesList'
import { SourcesList } from './SourcesList'
import { StatCards } from './StatCards'
import { TransactionsList } from './TransactionsList'

export type DataPart = {
  type: string
  data: unknown
}

type RenderDirectivesProps = {
  parts: DataPart[]
}

function GenericRenderCard({ part }: { part: DataPart }) {
  const label = part.type.replace(/^data-/, '').replace(/_/g, ' ')
  return (
    <details className="action-card action-card-generic">
      <summary>{label}</summary>
      {part.data != null ? (
        <pre className="action-raw">{JSON.stringify(part.data, null, 2)}</pre>
      ) : null}
    </details>
  )
}

function RenderPartItem({ part }: { part: DataPart }) {
  // `part.type` is `data-<component-id>` (e.g. "data-accounts-list").
  switch (part.type) {
    case 'data-accounts-list':
      return <AccountsList detail={part.data} />
    case 'data-categories-list':
      return <CategoriesList detail={part.data} />
    case 'data-places-list':
      return <PlacesList detail={part.data} />
    case 'data-transactions-list':
      return <TransactionsList detail={part.data} />
    case 'data-balance-adjustments-list':
      return <BalanceAdjustmentsList detail={part.data} />
    case 'data-period-summary':
      return <SummaryCard detail={part.data} />
    case 'data-charts':
      return <ChartsPanel detail={part.data} />
    case 'data-web-sources':
      return <SourcesList detail={part.data} />
    case 'data-data_table':
      return <DataTable detail={part.data} />
    case 'data-stat_cards':
      return <StatCards detail={part.data} />
    default:
      return <GenericRenderCard part={part} />
  }
}

export function RenderDirectives({ parts }: RenderDirectivesProps) {
  if (parts.length === 0) return null

  return (
    <div className="message-render">
      {parts.map((part, i) => (
        <RenderPartItem key={`${part.type}-${i}`} part={part} />
      ))}
    </div>
  )
}
