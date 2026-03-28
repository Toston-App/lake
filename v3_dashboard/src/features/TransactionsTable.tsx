import { useState } from "react";
import {
  Search,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
} from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useTransactions } from "@/api/hooks";
import { formatNumber } from "@/lib/utils";
import type { Transaction, TransactionFilters } from "@/types/api";

export function TransactionsTable() {
  const [filters, setFilters] = useState<TransactionFilters>({
    order: "desc",
    page: 1,
    size: 15,
  });
  const [searchInput, setSearchInput] = useState("");

  const { data, isLoading, isError } = useTransactions(filters);

  const handleSearch = () => {
    setFilters((prev) => ({ ...prev, search: searchInput || undefined, page: 1 }));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch();
  };

  const goToPage = (page: number) => {
    setFilters((prev) => ({ ...prev, page }));
  };

  if (isError) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="flex items-center gap-3 p-6">
          <AlertCircle className="text-destructive h-5 w-5" />
          <span className="text-sm">Failed to load transactions</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle className="text-base">Transactions</CardTitle>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="text-muted-foreground absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2" />
              <Input
                placeholder="Search..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={handleKeyDown}
                className="h-8 w-[200px] pl-8 text-sm"
              />
            </div>
            <Button variant="secondary" size="sm" onClick={handleSearch}>
              Search
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-0">
        {isLoading ? (
          <div className="space-y-3 px-6">
            {[...Array(8)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items && data.items.length > 0 ? (
                  data.items.map((tx: Transaction) => (
                    <TableRow key={`${tx.type}-${tx.id}`}>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {tx.date || "-"}
                      </TableCell>
                      <TableCell className="max-w-[300px] truncate text-sm">
                        {tx.description || "-"}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            tx.type === "expense"
                              ? "expense"
                              : tx.type === "income"
                                ? "income"
                                : "transfer"
                          }
                        >
                          {tx.type}
                        </Badge>
                      </TableCell>
                      <TableCell
                        className={`text-right font-mono text-sm font-medium ${
                          tx.type === "expense"
                            ? "text-expense"
                            : tx.type === "income"
                              ? "text-income"
                              : "text-transfer"
                        }`}
                      >
                        {tx.type === "expense" ? "-" : ""}
                        {formatNumber(tx.amount)}
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="text-muted-foreground py-8 text-center"
                    >
                      No transactions found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>

            {data && data.pages > 1 && (
              <div className="flex items-center justify-between px-6 pt-4">
                <span className="text-muted-foreground text-xs">
                  Page {data.page} of {data.pages} ({data.total} total)
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    disabled={data.page <= 1}
                    onClick={() => goToPage(data.page - 1)}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    disabled={data.page >= data.pages}
                    onClick={() => goToPage(data.page + 1)}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
