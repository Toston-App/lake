import { useMemo, useState } from 'react'
import type { PendingConfirmation } from '../types/chat'
import {
  buildConfirmToolArgs,
  fieldLabel,
  isEditablePreview,
  parseTransactionPreview,
  type TransactionPreview,
} from '../utils/transactionPreview'

type TransactionApprovalFormProps = {
  pending: PendingConfirmation
  onApprove: (toolArgs: Record<string, unknown>) => void
  onReject: () => void
  isConfirming: boolean
}

const EXPENSE_FIELDS = [
  'amount',
  'date',
  'description',
  'account_id',
  'category_id',
  'subcategory_id',
  'place_id',
] as const

const INCOME_FIELDS = [
  'amount',
  'date',
  'description',
  'account_id',
  'subcategory_id',
  'place_id',
] as const

const TRANSFER_FIELDS = [
  'amount',
  'date',
  'description',
  'from_acc',
  'to_acc',
] as const

function fieldsForPreview(preview: TransactionPreview): readonly string[] {
  if (preview.transaction_type === 'transfer') return TRANSFER_FIELDS
  if (preview.transaction_type === 'income') return INCOME_FIELDS
  return EXPENSE_FIELDS
}

function formatDisplayValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (key.endsWith('_id') || key === 'from_acc' || key === 'to_acc') {
    return String(value)
  }
  return String(value)
}

export function TransactionApprovalForm({
  pending,
  onApprove,
  onReject,
  isConfirming,
}: TransactionApprovalFormProps) {
  const preview = useMemo(
    () => parseTransactionPreview(pending),
    [pending],
  )

  const [edited, setEdited] = useState<Record<string, unknown>>(() =>
    preview ? { ...preview.fields } : {},
  )

  if (!preview) return null

  const editable = isEditablePreview(preview)
  const fieldKeys = fieldsForPreview(preview)

  function setField(key: string, raw: string) {
    setEdited((prev) => {
      const next = { ...prev }
      if (raw === '') {
        delete next[key]
        return next
      }
      if (
        key === 'amount' ||
        key.endsWith('_id') ||
        key === 'from_acc' ||
        key === 'to_acc'
      ) {
        const num = Number(raw)
        next[key] = Number.isNaN(num) ? raw : num
      } else {
        next[key] = raw
      }
      return next
    })
  }

  function handleApprove() {
    if (!preview) return
    onApprove(buildConfirmToolArgs(pending.tool_name, preview, edited))
  }

  return (
    <div className="tx-approval-form">
      {editable ? (
        <div className="tx-form-grid">
          {fieldKeys.map((key) => (
            <label key={key} className="tx-form-field">
              <span>{fieldLabel(key, preview.labels)}</span>
              <input
                type={
                  key === 'date'
                    ? 'date'
                    : key === 'amount' ||
                        key.endsWith('_id') ||
                        key === 'from_acc' ||
                        key === 'to_acc'
                      ? 'number'
                      : 'text'
                }
                value={
                  edited[key] !== undefined && edited[key] !== null
                    ? String(edited[key])
                    : ''
                }
                onChange={(e) => setField(key, e.target.value)}
                disabled={isConfirming}
                step={key === 'amount' ? '0.01' : undefined}
              />
            </label>
          ))}
        </div>
      ) : (
        <dl className="tx-readonly-grid">
          {fieldKeys.map((key) => (
            <div key={key} className="tx-readonly-row">
              <dt>{fieldLabel(key, preview.labels)}</dt>
              <dd>{formatDisplayValue(key, preview.fields[key])}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="tool-approval-actions">
        <button
          type="button"
          className="tool-approval-approve"
          onClick={handleApprove}
          disabled={isConfirming}
        >
          {isConfirming
            ? 'Saving…'
            : preview.operation === 'delete'
              ? 'Confirm delete'
              : 'Approve & save'}
        </button>
        <button
          type="button"
          className="tool-approval-reject"
          onClick={onReject}
          disabled={isConfirming}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
