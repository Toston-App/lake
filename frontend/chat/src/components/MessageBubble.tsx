import type { UIMessage } from '@ai-sdk/react'
import { MarkdownContent } from './MarkdownContent'
import { RenderDirectives } from './render/RenderDirectives'

type MessageBubbleProps = {
  message: UIMessage
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

  // Assistant: render only deliberate public text/data parts. Raw tool and
  // reasoning parts are ignored defensively even though the backend filters them.
  const textParts: string[] = []
  const renderParts: { type: string; data: unknown }[] = []

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
          break
        }
        if (typeof part.type === 'string' && part.type.startsWith('data-')) {
          // Pending-confirmation / pending-question are rendered in the dock
          // (App.tsx), while tool status is rendered by ThinkingPanel.
          if (
            part.type === 'data-pending-confirmation' ||
            part.type === 'data-pending-question' ||
            part.type === 'data-tool-status'
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
  if (!hasText && renderParts.length === 0) return null

  return (
    <div className="message message-assistant">
      <div className="message-bubble">
        {hasText && <MarkdownContent content={textParts.join('')} />}
        {renderParts.length > 0 ? (
          <RenderDirectives parts={renderParts} />
        ) : null}
      </div>
    </div>
  )
}
