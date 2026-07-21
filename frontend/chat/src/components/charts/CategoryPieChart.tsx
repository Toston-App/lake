import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import type { CategoryChartData } from '../../types/chat'

type CategoryPieChartProps = {
  data: CategoryChartData
}

export function CategoryPieChart({ data }: CategoryPieChartProps) {
  const slices = data.categories.map((c) => ({
    name: c.name,
    value: c.value,
    color: c.color ?? '#71717a',
  }))

  return (
    <div className="chart-card">
      <h4 className="chart-title">Spending by category</h4>
      <ResponsiveContainer width="100%" height={240}>
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={80}
            label={({ name, percent }) =>
              `${name} ${((percent ?? 0) * 100).toFixed(0)}%`
            }
          >
            {slices.map((entry) => (
              <Cell key={entry.name} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}
