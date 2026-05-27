import { apiFetch } from './client'
import type {
  ChatConfirmRequest,
  ChatMessageRequest,
  ChatMessageResponse,
  ConversationDetail,
  ConversationSummary,
} from '../types/chat'

const CHAT_PREFIX = '/api/v1/chat'

export function sendMessage(
  body: ChatMessageRequest,
): Promise<ChatMessageResponse> {
  return apiFetch<ChatMessageResponse>(`${CHAT_PREFIX}/messages`, {
    method: 'POST',
    body: JSON.stringify(body),
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
