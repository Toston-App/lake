import type { PendingConfirmation } from '../types/chat'
import type { ChatLanguage } from './i18n'

export type FieldChoice = {
  value: number
  label: string
  parent_value?: number
}

export type TransactionPreview = {
  kind: string
  operation: 'create' | 'update' | 'delete' | 'other'
  transaction_type?: 'expense' | 'income' | 'transfer'
  record_id?: number
  fields: Record<string, unknown>
  labels?: Record<string, string>
  choices?: Record<string, FieldChoice[]>
  existing?: Record<string, unknown>
}

export function parseTransactionPreview(
  pending: PendingConfirmation,
): TransactionPreview | null {
  const p = pending.preview
  if (!p || typeof p !== 'object') return null
  if (
    'fields' in p &&
    p.fields &&
    typeof p.fields === 'object' &&
    'transaction_type' in p &&
    ['expense', 'income', 'transfer'].includes(String(p.transaction_type))
  ) {
    return p as unknown as TransactionPreview
  }
  return null
}

export function isEditablePreview(preview: TransactionPreview): boolean {
  return preview.operation === 'create' || preview.operation === 'update'
}

function allowedFieldKeys(preview: TransactionPreview): string[] {
  if (preview.transaction_type === 'transfer') {
    return ['amount', 'date', 'description', 'from_acc', 'to_acc']
  }
  if (preview.transaction_type === 'income') {
    return ['amount', 'date', 'description', 'account_id', 'subcategory_id', 'place_id']
  }
  return [
    'amount',
    'date',
    'description',
    'account_id',
    'category_id',
    'subcategory_id',
    'place_id',
  ]
}

export function buildConfirmToolArgs(
  toolName: string,
  preview: TransactionPreview,
  edited: Record<string, unknown>,
): Record<string, unknown> {
  const allowed = allowedFieldKeys(preview)
  const payload: Record<string, unknown> = {}
  for (const key of allowed) {
    const value = edited[key]
    if (value === '' || value === undefined || value === null) continue
    payload[key] = value
  }

  if (toolName.startsWith('update_')) {
    return {
      id: preview.record_id,
      data: payload,
    }
  }

  if (toolName.startsWith('create_')) {
    payload.made_from = 'Chat'
  }

  return payload
}

export function fieldLabel(
  key: string,
  labels?: Record<string, string>,
  language: ChatLanguage = 'en',
): string {
  const human: Record<ChatLanguage, Record<string, string>> = {
    en: {
      amount: 'Amount',
      date: 'Date',
      description: 'Description',
      account_id: 'Account',
      category_id: 'Category',
      subcategory_id: 'Subcategory',
      place_id: 'Place',
      from_acc: 'From account',
      to_acc: 'To account',
      new_balance: 'New balance',
      adjustment_date: 'Date',
    },
    es: {
      amount: 'Monto',
      date: 'Fecha',
      description: 'Descripción',
      account_id: 'Cuenta',
      category_id: 'Categoría',
      subcategory_id: 'Subcategoría',
      place_id: 'Lugar',
      from_acc: 'Cuenta de origen',
      to_acc: 'Cuenta de destino',
      new_balance: 'Nuevo saldo',
      adjustment_date: 'Fecha',
    },
  }
  const base = human[language][key] ?? key.replace(/_/g, ' ')
  const resolved = labels?.[key]
  return resolved ? `${base} (${resolved})` : base
}
