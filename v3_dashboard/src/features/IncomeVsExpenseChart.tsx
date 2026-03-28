import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCharts } from "@/api/hooks";
import type { DateFilterType, TransactionChartData } from "@/types/api";

interface IncomeVsExpenseChartProps {
  dateFilterType: DateFilterType;
  date: string;
}

export function IncomeVsExpenseChart({
  dateFilterType,
  date,
}: IncomeVsExpenseChartProps) {
  const { data, isLoading } = useCharts(dateFilterType, date);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Income vs Expense</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[300px] w-full" />
        </CardContent>
      </Card>
    );
  }

  const chartData = data?.income_vs_expense as
    | TransactionChartData
    | undefined;

  if (
    !chartData ||
    Array.isArray(chartData) ||
    !chartData.series ||
    !chartData.xAxis
  ) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Income vs Expense</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground py-12 text-center text-sm">
            No transaction data for this period
          </p>
        </CardContent>
      </Card>
    );
  }

  // Transform from series format to recharts format
  const rechartsData = chartData.xAxis.map((label, i) => {
    const point: Record<string, string | number> = { label };
    chartData.series.forEach((s) => {
      point[s.name] = s.data[i] ?? 0;
    });
    return point;
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Income vs Expense</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart
            data={rechartsData}
            margin={{ top: 5, right: 10, left: 10, bottom: 5 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--color-border)"
            />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
              tickLine={false}
              axisLine={{ stroke: "var(--color-border)" }}
            />
            <YAxis
              tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
              tickLine={false}
              axisLine={false}
              width={60}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "var(--color-card)",
                border: "1px solid var(--color-border)",
                borderRadius: "8px",
                fontSize: "13px",
              }}
              labelStyle={{ color: "var(--color-foreground)" }}
            />
            <Legend wrapperStyle={{ fontSize: "13px" }} />
            {chartData.series.map((s) => (
              <Line
                key={s.name}
                type="monotone"
                dataKey={s.name}
                stroke={s.color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0 }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
