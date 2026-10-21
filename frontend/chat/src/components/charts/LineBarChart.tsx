import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { LineBarChartData } from '../../types/chat'

type LineBarChartProps = {
  title: string
  data: LineBarChartData
}

export function LineBarChart({ title, data }: LineBarChartProps) {
  const rows = data.xAxis.map((label, i) => {
    const row: Record<string, string | number> = { label }
    for (const series of data.series) {
      row[series.name] = series.data[i] ?? 0
    }
    return row
  })

  return (
    <div className="chart-card">
      <h4 className="chart-title">{title}</h4>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {data.series.map((s) => (
            <Line
              key={s.name}
              type="monotone"
              dataKey={s.name}
              stroke={s.color ?? '#168fff'}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
