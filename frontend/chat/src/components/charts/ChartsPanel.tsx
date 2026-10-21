import {
  hasChartData,
  isCategoryChartData,
  isLineBarChartData,
  parseChartsResponse,
} from '../../utils/charts'
import { CategoryPieChart } from './CategoryPieChart'
import { LineBarChart } from './LineBarChart'

type ChartsPanelProps = {
  detail: unknown
}

export function ChartsPanel({ detail }: ChartsPanelProps) {
  const charts = parseChartsResponse(detail)
  if (!charts || !hasChartData(charts)) {
    return <p className="action-empty">No chart data for this period.</p>
  }

  return (
    <div className="charts-panel">
      {isLineBarChartData(charts.net) && charts.net.xAxis.length > 0 && (
        <LineBarChart title="Net over time" data={charts.net} />
      )}
      {isLineBarChartData(charts.income_vs_expense) &&
        charts.income_vs_expense.xAxis.length > 0 && (
          <LineBarChart
            title="Income vs expenses"
            data={charts.income_vs_expense}
          />
        )}
      {isCategoryChartData(charts.categories) &&
        charts.categories.categories.length > 0 && (
          <CategoryPieChart data={charts.categories} />
        )}
    </div>
  )
}
