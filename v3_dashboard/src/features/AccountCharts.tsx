import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCharts, useAccounts } from "@/api/hooks";
import type { DateFilterType, AccountChartItem } from "@/types/api";

interface AccountChartsProps {
  dateFilterType: DateFilterType;
  date: string;
}

export function AccountCharts({ dateFilterType, date }: AccountChartsProps) {
  const { data: chartsData, isLoading: chartsLoading } = useCharts(
    dateFilterType,
    date,
  );
  const { data: accounts, isLoading: accountsLoading } = useAccounts();

  if (chartsLoading || accountsLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Account Balances</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[300px] w-full" />
        </CardContent>
      </Card>
    );
  }

  const accountCharts = chartsData?.accounts;

  if (!accountCharts || Object.keys(accountCharts).length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Account Balances</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground py-12 text-center text-sm">
            No account chart data for this period
          </p>
        </CardContent>
      </Card>
    );
  }

  // Map account IDs to names
  const accountMap = new Map(
    accounts?.map((a) => [String(a.id), a]) || [],
  );

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {Object.entries(accountCharts).map(([accountId, chartItem]) => {
        const item = chartItem as AccountChartItem;
        const account = accountMap.get(accountId);
        const accountName = account?.name || `Account #${accountId}`;
        const accountColor = account?.color || "#3b82f6";

        if (!item.xAxis?.data?.length) return null;

        const rechartsData = item.xAxis.data.map(
          (date: string, i: number) => ({
            date,
            balance: item.series.data[i] ?? 0,
          }),
        );

        return (
          <Card key={accountId}>
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <div
                  className="h-3 w-3 rounded-full"
                  style={{ backgroundColor: accountColor }}
                />
                <CardTitle className="text-sm font-medium">
                  {accountName}
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={180}>
                <AreaChart
                  data={rechartsData}
                  margin={{ top: 5, right: 10, left: 10, bottom: 5 }}
                >
                  <defs>
                    <linearGradient
                      id={`gradient-${accountId}`}
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="5%"
                        stopColor={accountColor}
                        stopOpacity={0.3}
                      />
                      <stop
                        offset="95%"
                        stopColor={accountColor}
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--color-border)"
                  />
                  <XAxis
                    dataKey="date"
                    tick={{
                      fontSize: 10,
                      fill: "var(--color-muted-foreground)",
                    }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tick={{
                      fontSize: 10,
                      fill: "var(--color-muted-foreground)",
                    }}
                    tickLine={false}
                    axisLine={false}
                    width={50}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "var(--color-card)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "8px",
                      fontSize: "12px",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="balance"
                    stroke={accountColor}
                    strokeWidth={2}
                    fill={`url(#gradient-${accountId})`}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
