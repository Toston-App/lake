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

  const updateStreamingAssistant = useCallback(
    (
      updater: (msg: UiMessage) => UiMessage,
    ) => {
      setMessages((prev) => {
        const idx = prev.findLastIndex(
          (m) => m.role === 'assistant' && m.streaming,
        )
        if (idx < 0) return prev
        const next = [...prev]
        next[idx] = updater(next[idx])
        return next
      })
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
      const streamingPlaceholder: UiMessage = {
        role: 'assistant',
        content: '',
        streaming: true,
        toolSteps: [],
      }
      setMessages((prev) => [...prev, userMessage, streamingPlaceholder])
      setIsLoading(true)

      let streamFailed = false

      try {
        await chatApi.sendMessageStream(
          {
            message: trimmed,
            conversation_id: conversationId ?? undefined,
          },
          {
            onMeta: (id) => setConversationId(id),
            onToken: (delta) => {
              updateStreamingAssistant((m) => ({
                ...m,
                content: m.content + delta,
              }))
            },
            onToolStep: (step) => {
              updateStreamingAssistant((m) => ({
                ...m,
                toolSteps: [...(m.toolSteps ?? []), step],
              }))
            },
            onDone: (response) => {
              setConversationId(response.conversation_id)
              setMessages((prev) => {
                const idx = prev.findLastIndex(
                  (m) => m.role === 'assistant' && m.streaming,
                )
                if (idx < 0) return prev
                const next = [...prev]
                next[idx] = {
                  role: 'assistant',
                  content: response.reply,
                  actions:
                    response.actions.length > 0
                      ? response.actions
                      : undefined,
                  toolSteps:
                    response.tool_steps.length > 0
                      ? response.tool_steps
                      : undefined,
                  streaming: false,
                }
                return next
              })
              if (response.pending_confirmation) {
                setPendingConfirmation(response.pending_confirmation)
              }
            },
            onError: (detail) => {
              streamFailed = true
              setError(detail)
            },
          },
        )
        if (!streamFailed) {
          await refreshConversations()
        }
      } catch (err) {
        setMessages((prev) => prev.slice(0, -2))
        setError(formatError(err))
      } finally {
        if (streamFailed) {
          setMessages((prev) => prev.slice(0, -2))
        }
        setIsLoading(false)
      }
    },
    [
      conversationId,
      formatError,
      isLoading,
      refreshConversations,
      updateStreamingAssistant,
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
