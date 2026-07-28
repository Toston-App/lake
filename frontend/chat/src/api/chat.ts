import { DefaultChatTransport } from 'ai'
import { apiFetch, getApiBaseUrl, getToken } from './client'
import type {
  ChatConfirmRequest,
  ChatMessageResponse,
  ConversationDetail,
  ConversationSummary,
} from '../types/chat'

const CHAT_PREFIX = '/api/v1/chat'

/**
 * Vercel AI SDK transport for `useChat`. Targets the Pydantic AI
 * `VercelAIAdapter` endpoint at POST /api/v1/chat (data-stream protocol).
 *
 * The chat `id` (useChat's `id`) is sent as the request body `id` field, which
 * the backend reads as `conversation_id`. Auth is the same bearer token used by
 * the REST calls. A custom `fetch` intercepts 402 quota-exceeded responses and
 * forwards the structured `detail` to `on402` so the UI shows the upgrade
 * banner instead of a generic stream error.
 */
export function chatTransport(on402?: (detail: unknown) => void) {
  return new DefaultChatTransport({
    api: `${getApiBaseUrl()}${CHAT_PREFIX}`,
    credentials: 'include',
    headers: (): Record<string, string> => {
      const token = getToken()
      return token ? { Authorization: `Bearer ${token}` } : {}
    },
    prepareSendMessagesRequest: ({ id, messages, trigger }) => ({
      // Conversation context is server-owned. Only the newest user turn crosses
      // the wire, so private tool/reasoning history never needs to round-trip
      // through the browser.
      body: { id, messages: messages.slice(-1), trigger },
    }),
    fetch: async (input, init) => {
      const res = await fetch(input, init)
      if (res.status === 402 && on402) {
        try {
          const body = await res.clone().json()
          on402(body.detail)
        } catch {
          // ignore parse errors; the generic stream error will surface
        }
      }
      return res
    },
  })
}

export function listConversations(): Promise<ConversationSummary[]> {
  return apiFetch<ConversationSummary[]>(`${CHAT_PREFIX}/conversations`)
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`${CHAT_PREFIX}/conversations/${id}`)
}

export function deleteConversation(id: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`${CHAT_PREFIX}/conversations/${id}`, {
    method: 'DELETE',
  })
}

export function confirmAction(
  conversationId: string,
  body: ChatConfirmRequest,
): Promise<ChatMessageResponse> {
  return apiFetch<ChatMessageResponse>(
    `${CHAT_PREFIX}/conversations/${conversationId}/confirm`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}
