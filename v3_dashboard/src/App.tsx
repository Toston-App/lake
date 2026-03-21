import { useState } from "react";
import { LayoutDashboard } from "lucide-react";
import { Separator } from "@/components/ui/separator";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { DateFilter } from "@/features/DateFilter";
import { SummaryCards } from "@/features/SummaryCards";
import { AccountsList } from "@/features/AccountsList";
import { NetChart } from "@/features/NetChart";
import { IncomeVsExpenseChart } from "@/features/IncomeVsExpenseChart";
import { CategoriesChart } from "@/features/CategoriesChart";
import { AccountCharts } from "@/features/AccountCharts";
import { TransactionsTable } from "@/features/TransactionsTable";
import { ComparisonView } from "@/features/ComparisonView";
import { getCurrentMonth } from "@/lib/utils";
import type { DateFilterType } from "@/types/api";

export default function App() {
  const [dateFilterType, setDateFilterType] = useState<DateFilterType>("month");
  const [date, setDate] = useState(getCurrentMonth);
  const [chartTab, setChartTab] = useState("net");

  return (
    <div className="bg-background text-foreground min-h-screen">
      {/* ── Top bar ── */}
      <header className="border-border sticky top-0 z-10 border-b bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <LayoutDashboard className="text-primary h-5 w-5" />
            <h1 className="text-lg font-semibold tracking-tight">Dashboard</h1>
          </div>

          <DateFilter
            dateFilterType={dateFilterType}
            date={date}
            onDateFilterTypeChange={setDateFilterType}
            onDateChange={setDate}
          />
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="mx-auto max-w-7xl space-y-8 px-6 py-8">
        {/* Row 1: Summary cards */}
        <section>
          <SummaryCards dateFilterType={dateFilterType} date={date} />
        </section>

        {/* Row 2: Accounts list */}
        <section>
          <h2 className="text-muted-foreground mb-3 text-xs font-semibold uppercase tracking-wider">
            Accounts
          </h2>
          <AccountsList />
        </section>

        <Separator />

        {/* Row 3: Charts (tabbed) */}
        <section>
          <Tabs value={chartTab} onValueChange={setChartTab}>
            <TabsList>
              <TabsTrigger value="net">Net</TabsTrigger>
              <TabsTrigger value="income-vs-expense">Income vs Expense</TabsTrigger>
              <TabsTrigger value="categories">Categories</TabsTrigger>
              <TabsTrigger value="accounts">Accounts</TabsTrigger>
            </TabsList>

            <TabsContent value="net">
              <NetChart dateFilterType={dateFilterType} date={date} />
            </TabsContent>
            <TabsContent value="income-vs-expense">
              <IncomeVsExpenseChart dateFilterType={dateFilterType} date={date} />
            </TabsContent>
            <TabsContent value="categories">
              <CategoriesChart dateFilterType={dateFilterType} date={date} />
            </TabsContent>
            <TabsContent value="accounts">
              <AccountCharts dateFilterType={dateFilterType} date={date} />
            </TabsContent>
          </Tabs>
        </section>

        <Separator />

        {/* Row 4: Transactions table */}
        <section>
          <TransactionsTable />
        </section>

        <Separator />

        {/* Row 5: Comparison view */}
        <section>
          <ComparisonView dateFilterType={dateFilterType} date={date} />
        </section>
      </main>
    </div>
  );
}
