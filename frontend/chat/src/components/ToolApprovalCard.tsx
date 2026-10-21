import { useEffect, useRef } from 'react'
import type { PendingConfirmation } from '../types/chat'
import { chatCopy, normalizeChatLanguage } from '../utils/i18n'
import { formatToolName } from '../utils/tools'
import {
  fieldLabel,
  isEditablePreview,
  parseTransactionPreview,
} from '../utils/transactionPreview'
import { MarkdownContent } from './MarkdownContent'
import { TransactionApprovalForm } from './TransactionApprovalForm'

type ToolApprovalCardProps = {
  pending: PendingConfirmation
  onApprove: (toolArgs?: Record<string, unknown>) => void
  onReject: () => void
  isConfirming: boolean
}

type PreviewItem = {
  transaction_type?: string
  fields?: Record<string, unknown>
  labels?: Record<string, string>
}

function transactionTypeLabel(value: string | undefined, language: 'en' | 'es') {
  const labels = {
    en: { expense: 'Expense', income: 'Income', transfer: 'Transfer' },
    es: { expense: 'Gasto', income: 'Ingreso', transfer: 'Transferencia' },
  }
  return value ? (labels[language][value as keyof typeof labels.en] ?? value) : null
}

function visibleEntries(
  fields: Record<string, unknown>,
  labels: Record<string, string> = {},
) {
  return Object.entries(fields)
    .filter(
      ([key, value]) =>
        value !== null &&
        value !== undefined &&
        key !== 'id' &&
        key !== 'record_id' &&
        key !== 'response_language' &&
        !key.endsWith('_id') &&
        key !== 'from_acc' &&
        key !== 'to_acc',
    )
    .map(([key, value]) => [key, String(value)] as const)
    .concat(
      Object.entries(labels)
        .filter(([, value]) => Boolean(value))
        .map(([key, value]) => [key, value] as const),
    )
}

export function ToolApprovalCard({
  pending,
  onApprove,
  onReject,
  isConfirming,
}: ToolApprovalCardProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const txPreview = parseTransactionPreview(pending)
  const hasTransactionForm = txPreview !== null
  const language = normalizeChatLanguage(pending.response_language)
  const copy = chatCopy(language)
  const preview = pending.preview
  const previewItems = Array.isArray(preview.items)
    ? (preview.items as PreviewItem[])
    : []
  const genericFields =
    preview.fields && typeof preview.fields === 'object'
      ? (preview.fields as Record<string, unknown>)
      : preview.changes && typeof preview.changes === 'object'
        ? (preview.changes as Record<string, unknown>)
        : {}
  const genericLabels =
    preview.labels && typeof preview.labels === 'object'
      ? (preview.labels as Record<string, string>)
      : {}

  useEffect(() => {
    cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [pending.action_id])

  return (
    <div ref={cardRef} className="tool-approval-card" role="alert">
      <div className="tool-approval-header">
        <span className="tool-approval-icon" aria-hidden>
          {txPreview?.operation === 'delete' ? '⚠' : '✎'}
        </span>
        <div>
          <h3 className="tool-approval-title">
            {String(preview.operation) === 'delete'
              ? copy.confirmDeletion
              : txPreview && isEditablePreview(txPreview)
                ? copy.reviewTransaction
                : copy.approvalRequired}
          </h3>
          <p className="tool-approval-subtitle">
            {formatToolName(pending.tool_name, language)}
          </p>
        </div>
      </div>

      <div className="tool-approval-body">
        <MarkdownContent content={pending.message} />
      </div>

      {hasTransactionForm ? (
        <TransactionApprovalForm
          key={pending.action_id}
          pending={pending}
          onApprove={(toolArgs) => onApprove(toolArgs)}
          onReject={onReject}
          isConfirming={isConfirming}
        />
      ) : (
        <>
          {preview.display_name ? (
            <p className="tool-approval-entity">
              {String(preview.display_name)}
            </p>
          ) : null}
          {previewItems.length > 0 ? (
            <div className="tx-readonly-grid" aria-label={copy.items}>
              {previewItems.map((item, index) => (
                <div className="tx-readonly-row" key={index}>
                  <dt>
                    {transactionTypeLabel(item.transaction_type, language) ??
                      `${copy.items} ${index + 1}`}
                  </dt>
                  <dd>
                    {visibleEntries(item.fields ?? {}, item.labels ?? {})
                      .map(([, value]) => value)
                      .join(' · ')}
                  </dd>
                </div>
              ))}
            </div>
          ) : Object.keys(genericFields).length > 0 ? (
            <dl className="tx-readonly-grid" aria-label={copy.changes}>
              {visibleEntries(genericFields, genericLabels).map(
                ([key, value]) => (
                  <div className="tx-readonly-row" key={key}>
                    <dt>{fieldLabel(key, undefined, language)}</dt>
                    <dd>{value}</dd>
                  </div>
                ),
              )}
            </dl>
          ) : null}
          <div className="tool-approval-actions">
            <button
              type="button"
              className="tool-approval-approve"
              onClick={() => onApprove()}
              disabled={isConfirming}
            >
              {isConfirming ? copy.running : copy.approve}
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
        </>
      )}
    </div>
  )
}
