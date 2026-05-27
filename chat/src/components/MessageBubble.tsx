import type { UiMessage } from '../types/chat'
import { MarkdownContent } from './MarkdownContent'
import { MessageActions } from './MessageActions'
import { ThinkingPanel } from './ThinkingPanel'
import { ToolStepsList } from './ToolStepsList'

type MessageBubbleProps = {
  message: UiMessage
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'

  return (
    <div className={`message message-${message.role}`}>
      <div className="message-bubble">
        {isUser ? (
          <p className="message-plain">{message.content}</p>
        ) : (
          <>
            {message.streaming ? (
              <>
                <ThinkingPanel
                  isActive
                  steps={
                    message.toolSteps && message.toolSteps.length > 0
                      ? message.toolSteps
                      : undefined
                  }
                />
                {message.content ? (
                  <MarkdownContent content={message.content} />
                ) : null}
              </>
            ) : (
              <>
                {message.toolSteps && message.toolSteps.length > 0 && (
                  <ToolStepsList steps={message.toolSteps} />
                )}
                <MarkdownContent content={message.content} />
                {message.actions && message.actions.length > 0 && (
                  <MessageActions actions={message.actions} />
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
