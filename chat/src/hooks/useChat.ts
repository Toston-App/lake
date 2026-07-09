import { useCallback, useMemo, useState } from 'react'
import { useChat as useAIChat, type UIMessage } from '@ai-sdk/react'
import {
  chatTransport,
  confirmAction,
  deleteConversation,
  getConversation,
  listConversations,
} from '../api/chat'
import { ApiError } from '../api/client'
import type {
  ConversationSummary,
  PendingConfirmation,
  PendingQuestion,
} from '../types/chat'
import type { QuotaErrorDetail } from '../types/subscription'

/** Find the most recent `data-<type>` part on any assistant message. */
function lastDataPart<T>(messages: UIMessage[], type: string): T | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role !== 'assistant') continue
    for (let j = m.parts.length - 1; j >= 0; j--) {
      const p = m.parts[j] as { type: string; data?: unknown }
      if (p.type === type && p.data != null) return p.data as T
    }
  }
  return null
}

export function useChat() {
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [isConfirming, setIsConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [quotaError, setQuotaError] = useState<QuotaErrorDetail | null>(null)
  // Action ids the user has confirmed/cancelled so their approval cards stop
  // re-rendering from the persisted `data-pending-confirmation` part.
  const [dismissedActions, setDismissedActions] = useState<Set<string>>(
    () => new Set(),
  )

  const formatError = useCallback((err: unknown): string => {
    if (err instanceof ApiError) {
      if (err.status === 503) return 'Chat is disabled on the server.'
      return err.detail
    }
    if (err instanceof Error) return err.message
    return 'Something went wrong'
  }, [])

  const maybeQuotaError = useCallback((err: unknown): boolean => {
    if (err instanceof ApiError && err.status === 402) {
      try {
        const parsed = JSON.parse(err.detail) as QuotaErrorDetail
        if (parsed && parsed.usage) {
          setQuotaError(parsed)
          return true
        }
      } catch {
        // fall through
      }
    }
    return false
  }, [])

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await listConversations())
    } catch (err) {
      setError(formatError(err))
    }
  }, [formatError])

  // The transport intercepts 402 in its fetch wrapper and surfaces the
  // structured quota detail (so the UI shows the upgrade banner, not a generic
  // "stream failed"). Created once; getToken() is read per-request.
  const transport = useMemo(
    () =>
      chatTransport((detail) => {
        if (detail) setQuotaError(detail as QuotaErrorDetail)
      }),
    [],
  )

  const {
    messages,
    sendMessage,
    status,
    error: chatError,
    setMessages,
    stop,
  } = useAIChat({
    id: conversationId ?? undefined,
    transport,
    onError: (err) => {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    },
    onFinish: () => {
      void refreshConversations()
    },
  })

  const isLoading = status === 'submitted' || status === 'streaming'

  const loadConversations = useCallback(async () => {
    setError(null)
    try {
      await refreshConversations()
    } catch (err) {
      setError(formatError(err))
    }
  }, [formatError, refreshConversations])

  const startNewChat = useCallback(() => {
    setConversationId(null)
    setMessages([])
    setDismissedActions(new Set())
    setError(null)
  }, [setMessages])

  const selectConversation = useCallback(
    async (id: string) => {
      setError(null)
      setDismissedActions(new Set())
      setConversationId(id)
      try {
        const detail = await getConversation(id)
        setMessages(detail.ui_messages ?? [])
      } catch (err) {
        setError(formatError(err))
      }
    },
    [formatError, setMessages],
  )

  const sendMessageText = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || isLoading) return
      setError(null)
      setQuotaError(null)
      void sendMessage({ text: trimmed })
    },
    [isLoading, sendMessage],
  )

  const rawPendingConfirmation = lastDataPart<PendingConfirmation>(
    messages,
    'data-pending-confirmation',
  )
  const pendingConfirmation =
    rawPendingConfirmation &&
    !dismissedActions.has(rawPendingConfirmation.action_id)
      ? rawPendingConfirmation
      : null
  const pendingQuestion = lastDataPart<PendingQuestion>(
    messages,
    'data-pending-question',
  )

  const confirmPending = useCallback(
    async (toolArgs?: Record<string, unknown>) => {
      if (!pendingConfirmation || !conversationId || isConfirming) return
      setError(null)
      setQuotaError(null)
      setIsConfirming(true)
      try {
        const res = await confirmAction(conversationId, {
          action_id: pendingConfirmation.action_id,
          tool_args: toolArgs,
        })
        setDismissedActions(
          (prev) => new Set(prev).add(pendingConfirmation.action_id),
        )
        if (res.ui_message) {
          setMessages((prev) => [...prev, res.ui_message as UIMessage])
        }
        await refreshConversations()
      } catch (err) {
        if (!maybeQuotaError(err)) setError(formatError(err))
      } finally {
        setIsConfirming(false)
      }
    },
    [
      conversationId,
      formatError,
      isConfirming,
      maybeQuotaError,
      pendingConfirmation,
      refreshConversations,
      setMessages,
    ],
  )

  const dismissPending = useCallback(() => {
    if (pendingConfirmation) {
      setDismissedActions(
        (prev) => new Set(prev).add(pendingConfirmation.action_id),
      )
    }
  }, [pendingConfirmation])

  const answerQuestion = useCallback(
    (labels: string[]) => {
      const answer = labels.filter(Boolean).join(', ')
      if (!answer) return
      void sendMessageText(answer)
    },
    [sendMessageText],
  )

  const removeConversation = useCallback(
    async (id: string) => {
      setError(null)
      try {
        await deleteConversation(id)
        if (conversationId === id) startNewChat()
        await refreshConversations()
      } catch (err) {
        setError(formatError(err))
      }
    },
    [conversationId, formatError, refreshConversations, startNewChat],
  )

  const clearQuotaError = useCallback(() => setQuotaError(null), [])

  return {
    conversationId,
    messages,
    conversations,
    pendingConfirmation,
    pendingQuestion,
    isLoading,
    isConfirming,
    error: error ?? (chatError ? String(chatError) : null),
    setError,
    quotaError,
    clearQuotaError,
    loadConversations,
    startNewChat,
    selectConversation,
    sendMessage: sendMessageText,
    answerQuestion,
    confirmPending,
    dismissPending,
    removeConversation,
    stop,
  }
}
