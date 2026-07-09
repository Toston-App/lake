import { useEffect, useRef } from 'react'
import type { UIMessage } from '@ai-sdk/react'
import { MessageBubble } from './MessageBubble'
import { ThinkingPanel } from './ThinkingPanel'

type MessageListProps = {
  messages: UIMessage[]
  isLoading: boolean
}

export function MessageList({ messages, isLoading }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const hasStreamingMessage = messages.some((m) =>
    m.parts.some((p) => {
      const state = (p as { state?: string }).state
      return state === 'input-streaming' || state === 'streaming'
    }),
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  return (
    <div className="message-list">
      {messages.length === 0 && !isLoading && (
        <p className="message-placeholder">
          Ask about your accounts, expenses, or finances.
        </p>
      )}
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
      {isLoading && !hasStreamingMessage && (
        <div className="message message-assistant">
          <div className="message-bubble message-bubble-thinking">
            <ThinkingPanel isActive />
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
