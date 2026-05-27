import { apiFetch, ApiError, getApiBaseUrl, getToken } from './client'
import type {
  ChatConfirmRequest,
  ChatMessageRequest,
  ChatMessageResponse,
  ChatStreamHandlers,
  ConversationDetail,
  ConversationSummary,
  ToolStep,
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

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = 'message'
  let data: string | null = null
  for (const line of block.split('\n')) {
    if (line.startsWith('event: ')) {
      event = line.slice(7)
    } else if (line.startsWith('data: ')) {
      data = line.slice(6)
    }
  }
  if (data === null) return null
  return { event, data }
}

export async function sendMessageStream(
  body: ChatMessageRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  if (!token) {
    throw new ApiError(401, 'Not authenticated')
  }

  const url = `${getApiBaseUrl()}${CHAT_PREFIX}/messages/stream`
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const errBody = await response.json()
      detail =
        typeof errBody.detail === 'string'
          ? errBody.detail
          : JSON.stringify(errBody.detail ?? errBody)
    } catch {
      // ignore
    }
    throw new ApiError(response.status, detail)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('No response body')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  const dispatchBlock = (block: string) => {
    const parsed = parseSseBlock(block)
    if (!parsed) return
    const payload = JSON.parse(parsed.data) as Record<string, unknown>
    switch (parsed.event) {
      case 'meta':
        handlers.onMeta(String(payload.conversation_id))
        break
      case 'token':
        handlers.onToken(String(payload.delta ?? ''))
        break
      case 'tool_step':
        handlers.onToolStep(payload as ToolStep)
        break
      case 'done':
        handlers.onDone(payload as ChatMessageResponse)
        break
      case 'error':
        handlers.onError(String(payload.detail ?? 'Stream failed'))
        break
      default:
        break
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        if (block.trim()) dispatchBlock(block)
      }
    }
    if (buffer.trim()) dispatchBlock(buffer)
  } finally {
    reader.releaseLock()
  }
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
