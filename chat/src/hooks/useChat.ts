import { useCallback, useEffect, useState } from 'react'
import * as chatApi from '../api/chat'
import { ApiError } from '../api/client'
import type {
  ChatAction,
  ConversationSummary,
  PendingConfirmation,
  ToolStep,
  UiMessage,
} from '../types/chat'

function toUiMessages(
  messages: { role: string; content: string; created_at?: string }[],
): UiMessage[] {
  return messages
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m) => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
      created_at: m.created_at,
    }))
}

export function useChat() {
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [pendingConfirmation, setPendingConfirmation] =
    useState<PendingConfirmation | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const formatError = useCallback((err: unknown): string => {
    if (err instanceof ApiError) {
      if (err.status === 503) {
        return 'Chat is disabled on the server (CHAT_ENABLED=false).'
      }
      return err.detail
    }
    if (err instanceof Error) return err.message
    return 'Something went wrong'
  }, [])

  const refreshConversations = useCallback(async () => {
    try {
      const list = await chatApi.listConversations()
      setConversations(list)
    } catch (err) {
      setError(formatError(err))
    }
  }, [formatError])

  useEffect(() => {
    if (conversationId === null) return
    void refreshConversations()
  }, [conversationId, refreshConversations])

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
    setPendingConfirmation(null)
    setError(null)
  }, [])

  const selectConversation = useCallback(
    async (id: string) => {
      setError(null)
      setIsLoading(true)
      setPendingConfirmation(null)
      try {
        const detail = await chatApi.getConversation(id)
        setConversationId(detail.id)
        setMessages(toUiMessages(detail.messages))
      } catch (err) {
        setError(formatError(err))
      } finally {
        setIsLoading(false)
      }
    },
    [formatError],
  )

  const appendAssistantMessage = useCallback(
    (
      reply: string,
      actions: ChatAction[],
      toolSteps: ToolStep[],
    ) => {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: reply,
          actions: actions.length > 0 ? actions : undefined,
          toolSteps: toolSteps.length > 0 ? toolSteps : undefined,
        },
      ])
    },
    [],
  )

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || isLoading) return

      setError(null)
      setPendingConfirmation(null)
      const userMessage: UiMessage = { role: 'user', content: trimmed }
      setMessages((prev) => [...prev, userMessage])
      setIsLoading(true)

      try {
        const response = await chatApi.sendMessage({
          message: trimmed,
          conversation_id: conversationId ?? undefined,
        })
        setConversationId(response.conversation_id)
        appendAssistantMessage(
          response.reply,
          response.actions,
          response.tool_steps,
        )
        if (response.pending_confirmation) {
          setPendingConfirmation(response.pending_confirmation)
        }
        await refreshConversations()
      } catch (err) {
        setMessages((prev) => prev.slice(0, -1))
        setError(formatError(err))
      } finally {
        setIsLoading(false)
      }
    },
    [
      conversationId,
      formatError,
      isLoading,
      refreshConversations,
      appendAssistantMessage,
    ],
  )

  const confirmPending = useCallback(
    async (toolArgs?: Record<string, unknown>) => {
    if (!pendingConfirmation || !conversationId || isConfirming) return

    setError(null)
    setIsConfirming(true)
    try {
      const response = await chatApi.confirmAction(conversationId, {
        action_id: pendingConfirmation.action_id,
        tool_args: toolArgs,
      })
      setPendingConfirmation(null)
      appendAssistantMessage(
        response.reply,
        response.actions,
        response.tool_steps,
      )
      await refreshConversations()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setIsConfirming(false)
    }
  },
  [
    conversationId,
    formatError,
    isConfirming,
    pendingConfirmation,
    refreshConversations,
    appendAssistantMessage,
  ],
  )

  const dismissPending = useCallback(() => {
    setPendingConfirmation(null)
  }, [])

  const removeConversation = useCallback(
    async (id: string) => {
      setError(null)
      try {
        await chatApi.deleteConversation(id)
        if (conversationId === id) {
          startNewChat()
        }
        await refreshConversations()
      } catch (err) {
        setError(formatError(err))
      }
    },
    [conversationId, formatError, refreshConversations, startNewChat],
  )

  return {
    conversationId,
    messages,
    conversations,
    pendingConfirmation,
    isLoading,
    isConfirming,
    error,
    setError,
    loadConversations,
    startNewChat,
    selectConversation,
    sendMessage,
    confirmPending,
    dismissPending,
    removeConversation,
  }
}
