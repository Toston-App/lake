import { useMemo, useState } from 'react'
import type { PendingConfirmation } from '../types/chat'
import { chatCopy, normalizeChatLanguage } from '../utils/i18n'
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

function isReferenceField(key: string): boolean {
  return key.endsWith('_id') || key === 'from_acc' || key === 'to_acc'
}

function formatDisplayValue(
  key: string,
  value: unknown,
  labels?: Record<string, string>,
): string {
  if (value === null || value === undefined) return '—'
  if (isReferenceField(key)) return labels?.[key] ?? '—'
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
  const language = normalizeChatLanguage(pending.response_language)
  const copy = chatCopy(language)

  function setField(key: string, raw: string) {
    setEdited((prev) => {
      const next = { ...prev }
      if (raw === '') {
        delete next[key]
        return next
      }
      if (
        key === 'amount' ||
        isReferenceField(key)
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
              <span>{fieldLabel(key, preview.labels, language)}</span>
              {isReferenceField(key) && preview.choices?.[key] ? (
                <select
                  value={
                    edited[key] !== undefined && edited[key] !== null
                      ? String(edited[key])
                      : ''
                  }
                  onChange={(e) => setField(key, e.target.value)}
                  disabled={isConfirming}
                >
                  <option value="">{copy.select}</option>
                  {preview.choices[key]
                    .filter(
                      (choice) =>
                        key !== 'subcategory_id' ||
                        choice.parent_value === undefined ||
                        edited.category_id === undefined ||
                        Number(edited.category_id) === choice.parent_value,
                    )
                    .map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                </select>
              ) : (
                <input
                  type={
                    key === 'date'
                      ? 'date'
                      : key === 'amount'
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
              )}
            </label>
          ))}
        </div>
      ) : (
        <dl className="tx-readonly-grid">
          {fieldKeys.map((key) => (
            <div key={key} className="tx-readonly-row">
              <dt>{fieldLabel(key, preview.labels, language)}</dt>
              <dd>
                {formatDisplayValue(key, preview.fields[key], preview.labels)}
              </dd>
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
            ? copy.saving
            : preview.operation === 'delete'
              ? copy.confirmDelete
              : copy.approveAndSave}
        </button>
        <button
          type="button"
          className="tool-approval-reject"
          onClick={onReject}
          disabled={isConfirming}
        >
          {copy.cancel}
        </button>
      </div>
    </div>
  )
}
