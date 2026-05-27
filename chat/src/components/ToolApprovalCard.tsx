import { useEffect, useRef, useState } from 'react'
import type { PendingConfirmation } from '../types/chat'
import { formatToolName } from '../utils/tools'
import {
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

export function ToolApprovalCard({
  pending,
  onApprove,
  onReject,
  isConfirming,
}: ToolApprovalCardProps) {
  const [showDetails, setShowDetails] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)
  const txPreview = parseTransactionPreview(pending)
  const hasTransactionForm = txPreview !== null

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
            {txPreview?.operation === 'delete'
              ? 'Confirm deletion'
              : txPreview && isEditablePreview(txPreview)
                ? 'Review transaction'
                : 'Approval required'}
          </h3>
          <p className="tool-approval-subtitle">
            {formatToolName(pending.tool_name)}
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
          <button
            type="button"
            className="tool-approval-details-toggle"
            onClick={() => setShowDetails((v) => !v)}
          >
            {showDetails ? 'Hide raw payload' : 'Show raw payload'}
          </button>
          {showDetails && (
            <pre className="tool-approval-raw">
              {JSON.stringify(pending.preview, null, 2)}
            </pre>
          )}
          <div className="tool-approval-actions">
            <button
              type="button"
              className="tool-approval-approve"
              onClick={() => onApprove()}
              disabled={isConfirming}
            >
              {isConfirming ? 'Running…' : 'Approve'}
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
        </>
      )}
    </div>
  )
}
