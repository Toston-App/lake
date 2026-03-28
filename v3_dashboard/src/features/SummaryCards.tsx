import {
  Wallet,
  TrendingUp,
  TrendingDown,
  ArrowUpDown,
  AlertCircle,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSummary } from "@/api/hooks";
import { formatNumber } from "@/lib/utils";
import type { DateFilterType } from "@/types/api";

interface SummaryCardsProps {
  dateFilterType: DateFilterType;
  date: string;
}

export function SummaryCards({ dateFilterType, date }: SummaryCardsProps) {
  const { data, isLoading, isError } = useSummary(dateFilterType, date);

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[...Array(4)].map((_, i) => (
          <Card key={i}>
            <CardContent className="p-6">
              <Skeleton className="mb-3 h-4 w-24" />
              <Skeleton className="h-8 w-32" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (isError || !data) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="flex items-center gap-3 p-6">
          <AlertCircle className="text-destructive h-5 w-5" />
          <span className="text-sm">Failed to load summary</span>
        </CardContent>
      </Card>
    );
  }

  const cards = [
    {
      label: "Total Balance",
      value: data.balance.total,
      icon: Wallet,
      color: "text-primary",
      bg: "bg-primary/10",
    },
    {
      label: "Period Income",
      value: data.period_income,
      icon: TrendingUp,
      color: "text-income",
      bg: "bg-income/10",
    },
    {
      label: "Period Expenses",
      value: data.period_expenses,
      icon: TrendingDown,
      color: "text-expense",
      bg: "bg-expense/10",
    },
    {
      label: "Period Net",
      value: data.period_net,
      icon: ArrowUpDown,
      color: data.period_net >= 0 ? "text-income" : "text-expense",
      bg: data.period_net >= 0 ? "bg-income/10" : "bg-expense/10",
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <Card key={card.label} className="overflow-hidden">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-sm font-medium">
                {card.label}
              </span>
              <div className={`rounded-lg p-2 ${card.bg}`}>
                <card.icon className={`h-4 w-4 ${card.color}`} />
              </div>
            </div>
            <p
              className={`mt-2 font-mono text-2xl font-bold tracking-tight ${card.color}`}
            >
              {formatNumber(card.value)}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
