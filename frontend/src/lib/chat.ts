/**
 * Chat API client, including the streaming reader.
 *
 * `EventSource` is the browser's built-in SSE client, and it is unusable here:
 * it only issues GET requests, and a chat message does not belong in a query
 * string. So the stream is read manually from `fetch`.
 */

import { API_BASE_URL, API_PREFIX, ApiError } from './api'

export interface ChatMessageDto {
  id: string
  role: 'user' | 'assistant'
  content: string
  position: number
  created_at: string
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
}

export interface ConversationDto {
  id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface ConversationDetailDto extends ConversationDto {
  messages: ChatMessageDto[]
}

/** The events the server sends inside the stream. Mirrors app/api/routes/chat.py. */
export type ChatEvent =
  | { type: 'start'; conversation_id: string; user_message_id: string }
  | { type: 'delta'; text: string }
  | {
      type: 'done'
      conversation_id: string
      message_id: string | null
      model: string | null
      input_tokens: number | null
      output_tokens: number | null
    }
  | { type: 'error'; code: string; message: string }

interface StreamOptions {
  content: string
  conversationId?: string | null
  onEvent: (event: ChatEvent) => void
  /** Lets the caller abort generation. Wired to the Stop button in step (e). */
  signal?: AbortSignal
}

/**
 * Send a message and invoke `onEvent` for each event as it arrives.
 *
 * Resolves when the stream ends. Rejects only if the request could not be
 * *started* -- once the response body is open the status is already 200, so
 * anything that goes wrong after that arrives as an `error` event instead.
 */
export async function streamChat({
  content,
  conversationId,
  onEvent,
  signal,
}: StreamOptions): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${API_PREFIX}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // Sends the httpOnly session cookie. Without it every request is anonymous
    // and the endpoint answers 401.
    credentials: 'include',
    body: JSON.stringify({
      content,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
    signal,
  })

  // Failures that happen *before* streaming begins -- 401, 404, 429, 422 --
  // come back as ordinary JSON, not as a stream.
  if (!response.ok) {
    let detail = `Request failed (HTTP ${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // A non-JSON error body is not worth failing over; the status is enough.
    }
    throw new ApiError(detail, response.status)
  }

  if (!response.body) {
    throw new ApiError('The browser did not provide a readable response.', 0)
  }

  const reader = response.body.getReader()
  // `stream: true` matters. A multi-byte character (an em dash, an emoji, any
  // non-ASCII text) can be split across two network chunks; decoding each
  // chunk independently turns it into replacement characters. This tells the
  // decoder to hold an incomplete sequence until the rest arrives.
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Events are separated by a blank line. A single read may contain
      // several events, or half of one -- so split off the complete ones and
      // keep the remainder for the next read.
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() ?? ''

      for (const chunk of chunks) {
        const line = chunk.trim()
        if (!line.startsWith('data:')) continue
        try {
          onEvent(JSON.parse(line.slice('data:'.length).trim()) as ChatEvent)
        } catch {
          // A malformed event must not kill an otherwise working stream.
          // Everything already delivered stays on screen.
        }
      }
    }
  } finally {
    // Releases the underlying connection whether the stream finished, threw,
    // or was aborted.
    reader.releaseLock()
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
    credentials: 'include',
  })
  if (!response.ok) {
    throw new ApiError(`Request failed (HTTP ${response.status})`, response.status)
  }
  return (await response.json()) as T
}

export function listConversations(): Promise<ConversationDto[]> {
  return getJson<ConversationDto[]>('/chat/conversations')
}

export function getConversation(id: string): Promise<ConversationDetailDto> {
  return getJson<ConversationDetailDto>(`/chat/conversations/${id}`)
}
