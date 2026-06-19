import type { RenderDirective } from '../../types/chat'
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

type RenderDirectivesProps = {
  directives: RenderDirective[]
}

function GenericRenderCard({ directive }: { directive: RenderDirective }) {
  return (
    <details className="action-card action-card-generic">
      <summary>{directive.component.replace(/_/g, ' ')}</summary>
      {directive.detail ? (
        <pre className="action-raw">
          {JSON.stringify(directive.detail, null, 2)}
        </pre>
      ) : null}
    </details>
  )
}

function RenderDirectiveItem({ directive }: { directive: RenderDirective }) {
  switch (directive.component) {
    case 'accounts_list':
      return <AccountsList detail={directive.detail} />
    case 'categories_list':
      return <CategoriesList detail={directive.detail} />
    case 'places_list':
      return <PlacesList detail={directive.detail} />
    case 'transactions_list':
      return <TransactionsList detail={directive.detail} />
    case 'balance_adjustments_list':
      return <BalanceAdjustmentsList detail={directive.detail} />
    case 'period_summary':
      return <SummaryCard detail={directive.detail} />
    case 'charts':
      return <ChartsPanel detail={directive.detail} />
    case 'web_sources':
      return <SourcesList detail={directive.detail} />
    case 'data_table':
      return <DataTable detail={directive.detail} />
    case 'stat_cards':
      return <StatCards detail={directive.detail} />
    default:
      return <GenericRenderCard directive={directive} />
  }
}

export function RenderDirectives({ directives }: RenderDirectivesProps) {
  if (directives.length === 0) return null

  return (
    <div className="message-render">
      {directives.map((directive, i) => (
        <RenderDirectiveItem
          key={`${directive.component}-${i}`}
          directive={directive}
        />
      ))}
    </div>
  )
}
