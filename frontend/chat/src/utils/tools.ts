import type { ChatLanguage } from './i18n'

const TOOL_LABELS: Record<ChatLanguage, Record<string, string>> = {
  en: {
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
  search_web: 'Web search',
  ask_user: 'Clarifying question',
  render_data: 'Rendering data',
  },
  es: {
    get_user_profile: 'Perfil',
    list_accounts: 'Ver cuentas',
    manage_account: 'Administrar cuenta',
    list_categories: 'Ver categorías',
    manage_category: 'Administrar categoría',
    manage_subcategory: 'Administrar subcategoría',
    list_places: 'Ver lugares',
    manage_place: 'Administrar lugar',
    create_expense: 'Crear gasto',
    update_expense: 'Actualizar gasto',
    delete_expense: 'Eliminar gasto',
    create_income: 'Crear ingreso',
    update_income: 'Actualizar ingreso',
    delete_income: 'Eliminar ingreso',
    create_transfer: 'Crear transferencia',
    update_transfer: 'Actualizar transferencia',
    delete_transfer: 'Eliminar transferencia',
    search_transactions: 'Buscar transacciones',
    get_period_summary: 'Resumen del periodo',
    get_period_charts: 'Gráficas del periodo',
    bulk_create_transactions: 'Crear varias transacciones',
    bulk_delete_transactions: 'Eliminar varias transacciones',
    create_balance_adjustment: 'Ajustar saldo',
    list_balance_adjustments: 'Ver ajustes',
    submit_feedback: 'Enviar comentarios',
    search_web: 'Buscar en la web',
    ask_user: 'Pregunta aclaratoria',
    render_data: 'Preparar datos',
  },
}

export function formatToolName(
  toolName: string,
  language: ChatLanguage = 'en',
): string {
  return TOOL_LABELS[language][toolName] ?? toolName.replace(/_/g, ' ')
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
