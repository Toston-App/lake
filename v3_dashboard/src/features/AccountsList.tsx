import {
  Landmark,
  PiggyBank,
  Banknote,
  TrendingUp,
  CreditCard,
  Building2,
  Briefcase,
  Gift,
  HelpCircle,
  AlertCircle,
  Coins,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccounts } from "@/api/hooks";
import { formatNumber } from "@/lib/utils";
import type { Account } from "@/types/api";

const ACCOUNT_ICONS: Record<string, React.ElementType> = {
  "Checking Accounts": Landmark,
  "Savings Accounts": PiggyBank,
  "Cash & Wallet": Banknote,
  "Stocks & Bonds": TrendingUp,
  "Crypto & Digital Assets": Coins,
  "Retirement Funds": Briefcase,
  "Loans & Mortgages": Building2,
  "Credit Cards": CreditCard,
  "Business Accounts": Building2,
  "Freelance & Side Income": Briefcase,
  "Prepaid & Gift Cards": Gift,
  Miscellaneous: HelpCircle,
};

function getAccountIcon(type?: string): React.ElementType {
  if (!type) return HelpCircle;
  return ACCOUNT_ICONS[type] || HelpCircle;
}

export function AccountsList() {
  const { data: accounts, isLoading, isError } = useAccounts();

  if (isLoading) {
    return (
      <div className="flex gap-4 overflow-x-auto pb-2">
        {[...Array(4)].map((_, i) => (
          <Card key={i} className="min-w-[220px] shrink-0">
            <CardContent className="p-5">
              <Skeleton className="mb-3 h-4 w-20" />
              <Skeleton className="h-6 w-28" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (isError || !accounts) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="flex items-center gap-3 p-6">
          <AlertCircle className="text-destructive h-5 w-5" />
          <span className="text-sm">Failed to load accounts</span>
        </CardContent>
      </Card>
    );
  }

  if (accounts.length === 0) {
    return (
      <Card>
        <CardContent className="text-muted-foreground p-6 text-center text-sm">
          No accounts found
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex gap-4 overflow-x-auto pb-2">
      {accounts.map((account: Account) => {
        const Icon = getAccountIcon(account.type);
        const balance = account.current_balance ?? 0;
        const isNegative = balance < 0;

        return (
          <Card key={account.id} className="min-w-[220px] shrink-0">
            <CardContent className="p-5">
              <div className="mb-3 flex items-center gap-2.5">
                <div
                  className="flex h-8 w-8 items-center justify-center rounded-lg"
                  style={{
                    backgroundColor: `${account.color || "#168FFF"}20`,
                  }}
                >
                  <Icon
                    className="h-4 w-4"
                    style={{ color: account.color || "#168FFF" }}
                  />
                </div>
                <span className="truncate text-sm font-medium">
                  {account.name || "Unnamed"}
                </span>
              </div>

              <p
                className={`font-mono text-lg font-bold ${isNegative ? "text-expense" : "text-foreground"}`}
              >
                {formatNumber(balance)}
              </p>

              <div className="text-muted-foreground mt-2 flex gap-4 text-xs">
                <span>
                  In{" "}
                  <span className="text-income font-medium">
                    {formatNumber(account.total_incomes ?? 0)}
                  </span>
                </span>
                <span>
                  Out{" "}
                  <span className="text-expense font-medium">
                    {formatNumber(account.total_expenses ?? 0)}
                  </span>
                </span>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
