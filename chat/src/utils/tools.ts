const TOOL_LABELS: Record<string, string> = {
  get_user_profile: 'Profile',
  list_accounts: 'List accounts',
  manage_account: 'Manage account',
  list_categories: 'List categories',
  manage_category: 'Manage category',
  manage_subcategory: 'Manage subcategory',
  list_places: 'List places',
  manage_place: 'Manage place',
  create_expense: 'Create expense',
  update_expense: 'Update expense',
  delete_expense: 'Delete expense',
  create_income: 'Create income',
  update_income: 'Update income',
  delete_income: 'Delete income',
  create_transfer: 'Create transfer',
  update_transfer: 'Update transfer',
  delete_transfer: 'Delete transfer',
  search_transactions: 'Search transactions',
  get_period_summary: 'Period summary',
  get_period_charts: 'Period charts',
  bulk_create_transactions: 'Bulk create',
  bulk_delete_transactions: 'Bulk delete',
  create_balance_adjustment: 'Balance adjustment',
  list_balance_adjustments: 'List adjustments',
  submit_feedback: 'Submit feedback',
}

export function formatToolName(toolName: string): string {
  return TOOL_LABELS[toolName] ?? toolName.replace(/_/g, ' ')
}

export function formatToolArgs(args: Record<string, unknown>): string {
  const parts: string[] = []
  if (args.date_filter_type && args.date) {
    parts.push(`${String(args.date_filter_type)} · ${String(args.date)}`)
  }
  if (args.amount != null) parts.push(`amount: ${args.amount}`)
  if (args.description) parts.push(String(args.description))
  if (args.id != null) parts.push(`id: ${args.id}`)
  if (args.action) parts.push(`action: ${args.action}`)
  if (parts.length > 0) return parts.join(' · ')
  return JSON.stringify(args, null, 2)
}

export const THINKING_PLACEHOLDERS = [
  'Understanding your question…',
  'Checking your accounts…',
  'Running tools…',
  'Preparing response…',
]
