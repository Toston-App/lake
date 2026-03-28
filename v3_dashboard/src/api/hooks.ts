import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type {
  Account,
  ChartsResponse,
  ComparisonResponse,
  DateFilterType,
  Page,
  SummaryResponse,
  Transaction,
  TransactionFilters,
} from "@/types/api";

// ─── Accounts ───

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => apiFetch<Account[]>("/accounts/"),
  });
}

// ─── Summary ───

export function useSummary(
  dateFilterType: DateFilterType,
  date: string,
) {
  return useQuery({
    queryKey: ["summary", dateFilterType, date],
    queryFn: () =>
      apiFetch<SummaryResponse>(`/summary/${dateFilterType}/${date}`),
    enabled: !!date,
  });
}

// ─── Charts ───

export function useCharts(
  dateFilterType: DateFilterType,
  date: string,
) {
  return useQuery({
    queryKey: ["charts", dateFilterType, date],
    queryFn: () =>
      apiFetch<ChartsResponse>(`/charts/${dateFilterType}/${date}`),
    enabled: !!date,
  });
}

// ─── Comparison ───

export function useComparison(
  dateFilterType: DateFilterType,
  date: string,
) {
  return useQuery({
    queryKey: ["comparison", dateFilterType, date],
    queryFn: () =>
      apiFetch<ComparisonResponse>(`/comparison/${dateFilterType}/${date}`),
    enabled: !!date,
  });
}

// ─── Transactions ───

export function useTransactions(filters: TransactionFilters) {
  const params = new URLSearchParams();

  if (filters.order) params.set("order", filters.order);
  if (filters.search) params.set("search", filters.search);
  if (filters.amount !== undefined) params.set("amount", String(filters.amount));
  if (filters.amount_operator) params.set("amount_operator", filters.amount_operator);
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  if (filters.page) params.set("page", String(filters.page));
  if (filters.size) params.set("size", String(filters.size));

  filters.accounts?.forEach((id) => params.append("accounts", String(id)));
  filters.categories?.forEach((id) => params.append("categories", String(id)));
  filters.places?.forEach((id) => params.append("places", String(id)));
  filters.transaction_type?.forEach((t) => params.append("transaction_type", t));

  const queryString = params.toString();
  const path = `/transactions/${queryString ? `?${queryString}` : ""}`;

  return useQuery({
    queryKey: ["transactions", filters],
    queryFn: () => apiFetch<Page<Transaction>>(path),
  });
}
