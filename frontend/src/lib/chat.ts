/**
 * Chat API client, including the streaming reader.
 *
 * `EventSource` is the browser's built-in SSE client, and it is unusable here:
 * it only issues GET requests, and a chat message does not belong in a query
 * string. So the stream is read manually from `fetch`.
 */

import { API_BASE_URL, API_PREFIX, ApiError, request } from './api'

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

/** One page of the sidebar list. Mirrors `ConversationPage` on the backend. */
export interface ConversationPageDto {
  items: ConversationDto[]
  limit: number
  offset: number
  has_more: boolean
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
  /** Lets the caller abort generation. Wired to the Stop button. */
  signal?: AbortSignal
}

interface RegenerateOptions {
  conversationId: string
  onEvent: (event: ChatEvent) => void
  signal?: AbortSignal
}

interface EditOptions {
  conversationId: string
  messageId: string
  content: string
  onEvent: (event: ChatEvent) => void
  signal?: AbortSignal
}

interface ListOptions {
  /** Free-text search over titles and message content. */
  query?: string
  limit?: number
  offset?: number
}

/** Read an SSE body, invoking `onEvent` for each complete event. */
async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const reader = body.getReader()
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

/** POST a JSON body and read the SSE response it returns. */
async function postStream(
  path: string,
  body: unknown,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${API_PREFIX}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // Sends the httpOnly session cookie. Without it every request is anonymous
    // and the endpoint answers 401.
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  })

  // Failures that happen *before* streaming begins -- 401, 404, 409, 429, 422
  // -- come back as ordinary JSON, not as a stream.
  if (!response.ok) {
    let detail = `Request failed (HTTP ${response.status})`
    try {
      const parsed = (await response.json()) as { detail?: unknown }
      if (typeof parsed.detail === 'string') detail = parsed.detail
    } catch {
      // A non-JSON error body is not worth failing over; the status is enough.
    }
    throw new ApiError(detail, response.status)
  }

  if (!response.body) {
    throw new ApiError('The browser did not provide a readable response.', 0)
  }

  await readEvents(response.body, onEvent)
}

/**
 * Send a message and invoke `onEvent` for each event as it arrives.
 *
 * Resolves when the stream ends. Rejects only if the request could not be
 * *started* -- once the response body is open the status is already 200, so
 * anything that goes wrong after that arrives as an `error` event instead.
 */
export function streamChat({
  content,
  conversationId,
  onEvent,
  signal,
}: StreamOptions): Promise<void> {
  return postStream(
    '/chat',
    { content, ...(conversationId ? { conversation_id: conversationId } : {}) },
    onEvent,
    signal,
  )
}

/**
 * Discard the last reply and stream a fresh answer to the same question.
 *
 * Deliberately sends no message text: the question already stored is re-used,
 * so a client cannot substitute a different one and have it attributed to the
 * original turn.
 */
export function regenerate({
  conversationId,
  onEvent,
  signal,
}: RegenerateOptions): Promise<void> {
  return postStream(
    '/chat/regenerate',
    { conversation_id: conversationId },
    onEvent,
    signal,
  )
}

/**
 * Rewrite one of your own questions and stream a new answer.
 *
 * Every turn after the edited one is discarded, on the server and on screen:
 * they were answering a question that is no longer there.
 */
export function editMessage({
  conversationId,
  messageId,
  content,
  onEvent,
  signal,
}: EditOptions): Promise<void> {
  return postStream(
    '/chat/edit',
    { conversation_id: conversationId, message_id: messageId, content },
    onEvent,
    signal,
  )
}

/** One page of conversations, optionally filtered by a search term. */
export async function listConversations({
  query,
  limit,
  offset,
}: ListOptions = {}): Promise<ConversationPageDto> {
  // URLSearchParams handles the encoding, so a term containing `&`, `#` or a
  // space cannot break out of the parameter it belongs to.
  const params = new URLSearchParams()
  if (query) params.set('q', query)
  if (limit !== undefined) params.set('limit', String(limit))
  if (offset !== undefined) params.set('offset', String(offset))

  const suffix = params.size > 0 ? `?${params}` : ''
  const { data } = await request<ConversationPageDto>(
    `/chat/conversations${suffix}`,
  )
  return data
}

export async function getConversation(id: string): Promise<ConversationDetailDto> {
  const { data } = await request<ConversationDetailDto>(`/chat/conversations/${id}`)
  return data
}

export async function renameConversation(
  id: string,
  title: string,
): Promise<ConversationDto> {
  const { data } = await request<ConversationDto>(`/chat/conversations/${id}`, {
    method: 'PATCH',
    body: { title },
  })
  return data
}

export async function deleteConversation(id: string): Promise<void> {
  await request<void>(`/chat/conversations/${id}`, { method: 'DELETE' })
}
