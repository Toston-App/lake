import { useEffect } from 'react'
import { ConversationSidebar } from './components/ConversationSidebar'
import { LoginForm } from './components/LoginForm'
import { MessageInput } from './components/MessageInput'
import { MessageList } from './components/MessageList'
import { ToolApprovalCard } from './components/ToolApprovalCard'
import { useAuth } from './hooks/useAuth'
import { useChat } from './hooks/useChat'

function App() {
  const { isAuthenticated, loginError, isLoggingIn, login, logout } = useAuth()
  const chat = useChat()
  const { loadConversations } = chat

  useEffect(() => {
    if (isAuthenticated) {
      void loadConversations()
    }
  }, [isAuthenticated, loadConversations])

  if (!isAuthenticated) {
    return (
      <div className="app app-login">
        <LoginForm
          onLogin={login}
          error={loginError}
          isLoading={isLoggingIn}
        />
      </div>
    )
  }

  return (
    <div className="app app-chat">
      <header className="chat-header">
        <h1>Toston Chat</h1>
        <button type="button" className="logout-btn" onClick={logout}>
          Log out
        </button>
      </header>
      <div className="chat-layout">
        <ConversationSidebar
          conversations={chat.conversations}
          activeId={chat.conversationId}
          onNewChat={chat.startNewChat}
          onSelect={(id) => void chat.selectConversation(id)}
          onDelete={(id) => void chat.removeConversation(id)}
        />
        <main className="chat-main">
          {chat.error && (
            <p className="error-banner chat-error">{chat.error}</p>
          )}
          <MessageList messages={chat.messages} isLoading={chat.isLoading} />
          {chat.pendingConfirmation && (
            <div className="approval-dock">
              <ToolApprovalCard
                pending={chat.pendingConfirmation}
                onApprove={(toolArgs) => void chat.confirmPending(toolArgs)}
                onReject={chat.dismissPending}
                isConfirming={chat.isConfirming}
              />
            </div>
          )}
          <MessageInput
            onSend={(text) => void chat.sendMessage(text)}
            disabled={
              chat.isLoading ||
              chat.isConfirming ||
              chat.pendingConfirmation !== null
            }
          />
        </main>
      </div>
    </div>
  )
}

export default App
