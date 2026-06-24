import { useEffect, useState } from 'react'
import { fetchMe } from './api/observability'
import { ApiError } from './api/client'
import { AccountView } from './components/AccountView'
import { ObservabilityDashboard } from './components/admin/ObservabilityDashboard'
import { ConversationSidebar } from './components/ConversationSidebar'
import { LoginForm } from './components/LoginForm'
import { MessageInput } from './components/MessageInput'
import { MessageList } from './components/MessageList'
import { QuestionChips } from './components/QuestionChips'
import { ToolApprovalCard } from './components/ToolApprovalCard'
import { UpgradeBanner } from './components/UpgradeBanner'
import { UsageIndicator } from './components/UsageIndicator'
import { useAuth } from './hooks/useAuth'
import { useChat } from './hooks/useChat'
import { useSubscription } from './hooks/useSubscription'

type View = 'chat' | 'admin' | 'account'

function App() {
  const { isAuthenticated, loginError, isLoggingIn, login, logout } = useAuth()
  const chat = useChat()
  const subscription = useSubscription(isAuthenticated)
  const { loadConversations } = chat
  const [isSuperuser, setIsSuperuser] = useState(false)
  const [view, setView] = useState<View>('chat')

  useEffect(() => {
    if (isAuthenticated) {
      void loadConversations()
      fetchMe()
        .then((me) => setIsSuperuser(me.is_superuser))
        .catch((err) => {
          // 403/401 just means no access; ignore other errors silently.
          if (err instanceof ApiError && err.status >= 500) {
            // eslint-disable-next-line no-console
            console.error('Failed to load user profile', err)
          }
        })
    } else {
      setIsSuperuser(false)
      setView('chat')
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

  if (view === 'admin' && isSuperuser) {
    return <ObservabilityDashboard onBack={() => setView('chat')} />
  }

  if (view === 'account') {
    return (
      <AccountView
        data={subscription.data}
        tiers={subscription.tiers}
        loading={subscription.loading}
        onChooseTier={(tier) => subscription.openCheckout(tier)}
        onManage={() => subscription.openPortal()}
        onBack={() => setView('chat')}
      />
    )
  }

  const openAccount = () => {
    chat.clearQuotaError()
    setView('account')
  }

  return (
    <div className="app app-chat">
      <header className="chat-header">
        <h1>Toston Chat</h1>
        <div className="chat-header-actions">
          <UsageIndicator data={subscription.data} onUpgrade={openAccount} />
          {isSuperuser && (
            <button
              type="button"
              className="logout-btn"
              onClick={() => setView('admin')}
            >
              Observability
            </button>
          )}
          <button
            type="button"
            className="logout-btn"
            onClick={openAccount}
          >
            Account
          </button>
          <button type="button" className="logout-btn" onClick={logout}>
            Log out
          </button>
        </div>
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
          {chat.quotaError && (
            <UpgradeBanner
              reason={chat.quotaError.reason}
              onUpgrade={openAccount}
            />
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
          {chat.pendingQuestion && !chat.pendingConfirmation && (
            <div className="approval-dock">
              <QuestionChips
                pending={chat.pendingQuestion}
                onAnswer={(labels) => chat.answerQuestion(labels)}
                disabled={chat.isLoading}
              />
            </div>
          )}
          <MessageInput
            onSend={(text) => void chat.sendMessage(text)}
            disabled={
              chat.isLoading ||
              chat.isConfirming ||
              chat.pendingConfirmation !== null ||
              chat.quotaError !== null
            }
          />
        </main>
      </div>
    </div>
  )
}

export default App
