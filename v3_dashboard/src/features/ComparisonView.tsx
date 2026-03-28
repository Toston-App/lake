import { TrendingUp, TrendingDown, Minus, AlertCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useComparison, useAccounts } from "@/api/hooks";
import { formatPercent } from "@/lib/utils";
import type { DateFilterType } from "@/types/api";

interface ComparisonViewProps {
  dateFilterType: DateFilterType;
  date: string;
}

export function ComparisonView({ dateFilterType, date }: ComparisonViewProps) {
  const { data, isLoading, isError } = useComparison(dateFilterType, date);
  const { data: accounts } = useAccounts();

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Period Comparison</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {[...Array(3)].map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="flex items-center gap-3 p-6">
          <AlertCircle className="text-destructive h-5 w-5" />
          <span className="text-sm">Failed to load comparison data</span>
        </CardContent>
      </Card>
    );
  }

  const growth = data?.accounts_growth;

  if (!growth || Object.keys(growth).length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Period Comparison</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground py-8 text-center text-sm">
            No comparison data available
          </p>
        </CardContent>
      </Card>
    );
  }

  // Map account IDs to names
  const accountMap = new Map(
    accounts?.map((a) => [String(a.id), a]) || [],
  );

  // Sort by absolute growth descending
  const entries = Object.entries(growth).sort(
    ([, a], [, b]) => Math.abs(b) - Math.abs(a),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Period Comparison</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {entries.map(([accountId, percent]) => {
          const account = accountMap.get(accountId);
          const accountName = account?.name || `Account #${accountId}`;
          const accountColor = account?.color || "#3b82f6";
          const isPositive = percent > 0;
          const isZero = percent === 0;

          // Clamp bar width to max 100%
          const barWidth = Math.min(Math.abs(percent), 100);

          return (
            <div key={accountId} className="flex items-center gap-4">
              <div className="flex min-w-[140px] items-center gap-2">
                <div
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: accountColor }}
                />
                <span className="truncate text-sm">{accountName}</span>
              </div>

              <div className="bg-secondary relative h-6 flex-1 overflow-hidden rounded-md">
                <div
                  className="absolute inset-y-0 left-0 rounded-md transition-all duration-500"
                  style={{
                    width: `${barWidth}%`,
                    backgroundColor: isZero
                      ? "var(--color-muted-foreground)"
                      : isPositive
                        ? "var(--color-income)"
                        : "var(--color-expense)",
                    opacity: 0.7,
                  }}
                />
              </div>

              <div className="flex min-w-[90px] items-center justify-end gap-1.5">
                {isZero ? (
                  <Minus className="text-muted-foreground h-3.5 w-3.5" />
                ) : isPositive ? (
                  <TrendingUp className="text-income h-3.5 w-3.5" />
                ) : (
                  <TrendingDown className="text-expense h-3.5 w-3.5" />
                )}
                <span
                  className={`font-mono text-sm font-medium ${
                    isZero
                      ? "text-muted-foreground"
                      : isPositive
                        ? "text-income"
                        : "text-expense"
                  }`}
                >
                  {formatPercent(percent)}
                </span>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
