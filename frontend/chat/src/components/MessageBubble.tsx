import type { UIMessage } from '@ai-sdk/react'
import { MarkdownContent } from './MarkdownContent'
import { RenderDirectives } from './render/RenderDirectives'
import { ThinkingPanel } from './ThinkingPanel'
import { ToolStepsList } from './ToolStepsList'
import type { ToolStep } from '../types/chat'
import { formatToolName } from '../utils/tools'

type MessageBubbleProps = {
  message: UIMessage
}

/** Map a tool-invocation part's state to a ToolStep for ToolStepsList/ThinkingPanel. */
function toolStepFromPart(part: {
  toolName?: string
  state?: string
}): ToolStep | null {
  if (!part.toolName) return null
  const state = part.state ?? 'input-available'
  const status =
    state === 'output-error'
      ? 'failed'
      : state === 'output-available'
        ? 'completed'
        : 'pending_confirmation'
  return {
    tool_name: part.toolName,
    status: status as ToolStep['status'],
    label: formatToolName(part.toolName),
  }
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'

  if (isUser) {
    const text = message.parts
      .map((p) => (p.type === 'text' ? p.text : ''))
      .join('')
    return (
      <div className="message message-user">
        <div className="message-bubble">
          <p className="message-plain">{text}</p>
        </div>
      </div>
    )
  }

  // Assistant: partition parts into text / tool-steps / data (render + pending).
  const textParts: string[] = []
  const toolSteps: ToolStep[] = []
  const renderParts: { type: string; data: unknown }[] = []
  let streaming = false

  for (const part of message.parts as Array<Record<string, unknown>>) {
    switch (part.type) {
      case 'text': {
        const t = (part as { text?: string }).text ?? ''
        if (t) textParts.push(t)
        break
      }
      case 'reasoning':
        break
      case 'step-start':
        break
      default: {
        if (typeof part.type === 'string' && part.type.startsWith('tool-')) {
          const step = toolStepFromPart(
            part as unknown as { toolName?: string; state?: string },
          )
          if (step) toolSteps.push(step)
          const state = (part as { state?: string }).state
          if (state === 'input-streaming' || state === 'input-available') {
            streaming = true
          }
          break
        }
        if (typeof part.type === 'string' && part.type.startsWith('data-')) {
          // Pending-confirmation / pending-question are rendered in the dock
          // (App.tsx), not inline; skip them here.
          if (
            part.type === 'data-pending-confirmation' ||
            part.type === 'data-pending-question'
          ) {
            break
          }
          renderParts.push({
            type: part.type,
            data: (part as { data?: unknown }).data,
          })
          break
        }
        break
      }
    }
  }

  const hasText = textParts.length > 0

  return (
    <div className="message message-assistant">
      <div className="message-bubble">
        {streaming && !hasText ? (
          <>
            <ThinkingPanel
              isActive
              steps={toolSteps.length > 0 ? toolSteps : undefined}
            />
            {textParts.length > 0 ? (
              <MarkdownContent content={textParts.join('')} />
            ) : null}
          </>
        ) : (
          <>
            {toolSteps.length > 0 && <ToolStepsList steps={toolSteps} />}
            {hasText && <MarkdownContent content={textParts.join('')} />}
            {renderParts.length > 0 ? (
              <RenderDirectives parts={renderParts} />
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
