import { useEffect, useRef } from 'react'
import type { UiMessage } from '../types/chat'
import { MessageBubble } from './MessageBubble'
import { ThinkingPanel } from './ThinkingPanel'

type MessageListProps = {
  messages: UiMessage[]
  isLoading: boolean
}

export function MessageList({ messages, isLoading }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const hasStreamingMessage = messages.some((m) => m.streaming)

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
      {messages.map((msg, index) => (
        <MessageBubble
          key={`${index}-${msg.role}-${msg.content.slice(0, 24)}`}
          message={msg}
        />
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
