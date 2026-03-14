// ─── Date filter types ───

export type DateFilterType =
  | "date"
  | "week"
  | "month"
  | "quarter"
  | "year"
  | "range";

// ─── Account ───

export interface Account {
  id: number;
  owner_id: number;
  name?: string;
  initial_balance?: number;
  current_balance?: number;
  total_expenses?: number;
  total_incomes?: number;
  total_transfers_in?: number;
  total_transfers_out?: number;
  type?: string;
  color?: string;
  import_id?: number;
}

// ─── Summary ───

export interface Balance {
  total: number;
  income: number;
  outcome: number;
}

export interface SummaryResponse {
  currency: string;
  language: string;
  balance: Balance;
  period_income: number;
  period_expenses: number;
  period_net: number;
}

// ─── Charts ───

export interface ChartSeries {
  name: string;
  data: number[];
  color: string;
}

export interface TransactionChartData {
  series: ChartSeries[];
  xAxis: string[];
}

export interface CategoryDrilldownItem {
  name: string;
  value: number;
}

export interface CategoryDrilldown {
  name: string;
  data: CategoryDrilldownItem[];
}

export interface CategoryItem {
  name: string;
  value: number;
  color: string;
}

export interface CategoriesChartData {
  drilldown: CategoryDrilldown[];
  categories: CategoryItem[];
}

export interface AccountChartItem {
  xAxis: { data: string[] };
  series: { data: number[] };
}

export interface ChartsResponse {
  transactions: TransactionChartData | [];
  categories: CategoriesChartData | null;
  accounts: Record<string, AccountChartItem>;
}

// ─── Comparison ───

export interface ComparisonResponse {
  accounts_growth: Record<string, number>;
}

// ─── Transactions ───

export interface ExpenseTransaction {
  id: number;
  amount: number;
  date?: string;
  description?: string;
  type: "expense";
  owner_id: number;
  account_id?: number;
  category_id?: number;
  subcategory_id?: number;
  place_id?: number;
}

export interface IncomeTransaction {
  id: number;
  amount: number;
  date?: string;
  description?: string;
  type: "income";
  owner_id: number;
  account_id?: number;
  subcategory_id?: number;
  place_id?: number;
}

export interface TransferTransaction {
  id: number;
  amount: number;
  date?: string;
  description?: string;
  type: "transfer";
  owner_id: number;
  from_acc?: number;
  to_acc?: number;
}

export type Transaction =
  | ExpenseTransaction
  | IncomeTransaction
  | TransferTransaction;

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

// ─── Filter types ───

export type TransactionType = "expense" | "income" | "transfer";
export type AmountOperator = "equal" | "less" | "greater";
export type OrderDirection = "asc" | "desc";

export interface TransactionFilters {
  order?: OrderDirection;
  search?: string;
  amount?: number;
  amount_operator?: AmountOperator;
  start_date?: string;
  end_date?: string;
  accounts?: number[];
  categories?: number[];
  places?: number[];
  transaction_type?: TransactionType[];
  page?: number;
  size?: number;
}
