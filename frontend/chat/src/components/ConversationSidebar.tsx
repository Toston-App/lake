import type { ConversationSummary } from '../types/chat'

type ConversationSidebarProps = {
  conversations: ConversationSummary[]
  activeId: string | null
  onNewChat: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

export function ConversationSidebar({
  conversations,
  activeId,
  onNewChat,
  onSelect,
  onDelete,
}: ConversationSidebarProps) {
  return (
    <aside className="sidebar">
      <button type="button" className="new-chat-btn" onClick={onNewChat}>
        New chat
      </button>
      <ul className="conversation-list">
        {conversations.length === 0 && (
          <li className="conversation-empty">No conversations yet</li>
        )}
        {conversations.map((conv) => (
          <li
            key={conv.id}
            className={
              conv.id === activeId ? 'conversation-item active' : 'conversation-item'
            }
          >
            <button
              type="button"
              className="conversation-select"
              onClick={() => onSelect(conv.id)}
            >
              <span className="conversation-title">{conv.title}</span>
              {conv.last_message_preview && (
                <span className="conversation-preview">
                  {conv.last_message_preview}
                </span>
              )}
              <span className="conversation-date">
                {formatDate(conv.updated_at)}
              </span>
            </button>
            <button
              type="button"
              className="conversation-delete"
              title="Delete conversation"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(conv.id)
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}
