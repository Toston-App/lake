import { useState } from "react";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCharts } from "@/api/hooks";
import { formatNumber } from "@/lib/utils";
import type { DateFilterType, CategoriesChartData } from "@/types/api";

interface CategoriesChartProps {
  dateFilterType: DateFilterType;
  date: string;
}

// Fallback colors when category doesn't have one
const FALLBACK_COLORS = [
  "#3b82f6",
  "#ef4444",
  "#10b981",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#f97316",
  "#84cc16",
  "#6366f1",
];

export function CategoriesChart({
  dateFilterType,
  date,
}: CategoriesChartProps) {
  const { data, isLoading } = useCharts(dateFilterType, date);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Categories</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[300px] w-full" />
        </CardContent>
      </Card>
    );
  }

  const chartData = data?.categories as CategoriesChartData | null | undefined;

  if (!chartData || !chartData.categories || chartData.categories.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Categories</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground py-12 text-center text-sm">
            No category data for this period
          </p>
        </CardContent>
      </Card>
    );
  }

  // Find drilldown data for the selected category
  const drilldownData = selectedCategory
    ? chartData.drilldown?.find((d) => d.name === selectedCategory)
    : null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">
            {selectedCategory
              ? `${selectedCategory} Breakdown`
              : "Categories"}
          </CardTitle>
          {selectedCategory && (
            <button
              onClick={() => setSelectedCategory(null)}
              className="text-muted-foreground hover:text-foreground cursor-pointer text-xs transition-colors"
            >
              Back to all
            </button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {drilldownData ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart
              data={drilldownData.data}
              layout="vertical"
              margin={{ top: 5, right: 20, left: 80, bottom: 5 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--color-border)"
                horizontal={false}
              />
              <XAxis
                type="number"
                tick={{
                  fontSize: 12,
                  fill: "var(--color-muted-foreground)",
                }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                tick={{
                  fontSize: 12,
                  fill: "var(--color-muted-foreground)",
                }}
                tickLine={false}
                axisLine={false}
                width={75}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "var(--color-card)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "8px",
                  fontSize: "13px",
                }}
                formatter={(value) => [formatNumber(Number(value)), "Amount"]}
              />
              <Bar
                dataKey="value"
                fill="var(--color-primary)"
                radius={[0, 4, 4, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex flex-col items-center gap-6 md:flex-row">
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={chartData.categories}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={100}
                  dataKey="value"
                  nameKey="name"
                  onClick={(entry) => setSelectedCategory(entry.name as string)}
                  style={{ cursor: "pointer" }}
                  stroke="var(--color-background)"
                  strokeWidth={2}
                >
                  {chartData.categories.map((entry, i) => (
                    <Cell
                      key={entry.name}
                      fill={entry.color || FALLBACK_COLORS[i % FALLBACK_COLORS.length]}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "8px",
                    fontSize: "13px",
                  }}
                  formatter={(value) => [
                    formatNumber(Number(value)),
                    "Amount",
                  ]}
                />
              </PieChart>
            </ResponsiveContainer>

            <div className="flex max-h-[300px] min-w-[180px] flex-col gap-2 overflow-y-auto pr-2">
              {chartData.categories.map((cat, i) => (
                <button
                  key={cat.name}
                  onClick={() => setSelectedCategory(cat.name)}
                  className="hover:bg-secondary/50 flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors"
                >
                  <div
                    className="h-3 w-3 shrink-0 rounded-full"
                    style={{
                      backgroundColor:
                        cat.color || FALLBACK_COLORS[i % FALLBACK_COLORS.length],
                    }}
                  />
                  <span className="flex-1 truncate text-xs">{cat.name}</span>
                  <span className="text-muted-foreground font-mono text-xs">
                    {formatNumber(cat.value)}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
