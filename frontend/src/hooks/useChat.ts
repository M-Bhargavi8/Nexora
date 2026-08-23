/**
 * State for one conversation: the messages, and what happens while a reply
 * is streaming.
 *
 * The interesting part is that the assistant's message exists on screen before
 * it exists on the server. It is created empty the moment the request goes out
 * and grown chunk by chunk, so the user sees text appear rather than a spinner
 * followed by a wall of prose.
 */

import { useCallback, useRef, useState } from 'react'
import { ApiError } from '../lib/api'
import {
  getConversation,
  regenerate as regenerateRequest,
  streamChat,
  type ChatEvent,
  type ChatMessageDto,
} from '../lib/chat'

/** A message on screen. `pending` marks the reply currently being streamed. */
export interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  pending?: boolean
  /** Set when this turn failed, so the UI can mark it rather than lose it. */
  failed?: boolean
}

export interface UseChat {
  messages: DisplayMessage[]
  conversationId: string | null
  isStreaming: boolean
  error: string | null
  /** Whether there is a completed answer that could be replaced. */
  canRegenerate: boolean
  send: (content: string) => Promise<void>
  regenerate: () => Promise<void>
  stop: () => void
  load: (conversationId: string) => Promise<void>
  reset: () => void
}

function toDisplay(message: ChatMessageDto): DisplayMessage {
  return { id: message.id, role: message.role, content: message.content }
}

/** Ids for messages that do not have a server id yet. */
let localCounter = 0
function localId(prefix: string): string {
  localCounter += 1
  return `${prefix}-local-${localCounter}`
}

export function useChat(): UseChat {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Held in a ref, not state: aborting must not depend on a re-render having
  // happened, and changing it should not cause one.
  const abortRef = useRef<AbortController | null>(null)
  // Also a ref. The streaming callback closes over its own render's state, so
  // reading `conversationId` there would see the value from when the send
  // started -- which is null on the very first message.
  const conversationIdRef = useRef<string | null>(null)

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    conversationIdRef.current = null
    setMessages([])
    setConversationId(null)
    setError(null)
    setIsStreaming(false)
  }, [])

  const load = useCallback(async (id: string) => {
    abortRef.current?.abort()
    setError(null)
    try {
      const conversation = await getConversation(id)
      conversationIdRef.current = conversation.id
      setConversationId(conversation.id)
      setMessages(conversation.messages.map(toDisplay))
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 404
          ? 'That conversation no longer exists.'
          : 'Could not load that conversation.',
      )
    }
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  /**
   * Drive one streamed reply into the message identified by `assistantId`.
   *
   * Shared by `send` and `regenerate`, which differ only in which request they
   * make and what they do to the transcript beforehand. Duplicating this would
   * mean two copies of the abort handling and the failure rules.
   */
  const consume = useCallback(
    async (
      assistantId: string,
      run: (
        onEvent: (event: ChatEvent) => void,
        signal: AbortSignal,
      ) => Promise<void>,
    ) => {
      const controller = new AbortController()
      abortRef.current = controller
      setError(null)
      setIsStreaming(true)

      const markAssistant = (update: Partial<DisplayMessage>) =>
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId ? { ...message, ...update } : message,
          ),
        )

      const handleEvent = (event: ChatEvent) => {
        switch (event.type) {
          case 'start':
            conversationIdRef.current = event.conversation_id
            setConversationId(event.conversation_id)
            break
          case 'delta':
            // Append rather than replace: each event carries one fragment, and
            // no fragment is meaningful on its own.
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, content: message.content + event.text }
                  : message,
              ),
            )
            break
          case 'error':
            setError(event.message)
            markAssistant({ pending: false, failed: true })
            break
          case 'done':
            markAssistant({ pending: false })
            break
        }
      }

      try {
        await run(handleEvent, controller.signal)
      } catch (caught) {
        if (controller.signal.aborted) {
          // The user pressed Stop. Whatever arrived stays on screen -- the
          // server has already saved it, so discarding it here would show less
          // than actually exists. Stopping is a choice, not a failure, so the
          // turn is not marked as failed and no error is shown.
          markAssistant({ pending: false })
        } else {
          setError(
            caught instanceof ApiError
              ? caught.message
              : 'Could not reach the assistant.',
          )
          markAssistant({ pending: false, failed: true })
        }
      } finally {
        setIsStreaming(false)
        abortRef.current = null
      }
    },
    [],
  )

  const send = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return

      const assistantId = localId('assistant')
      setMessages((current) => [
        ...current,
        { id: localId('user'), role: 'user', content: trimmed },
        { id: assistantId, role: 'assistant', content: '', pending: true },
      ])

      await consume(assistantId, (onEvent, signal) =>
        streamChat({
          content: trimmed,
          conversationId: conversationIdRef.current,
          onEvent,
          signal,
        }),
      )
    },
    [consume],
  )

  const regenerate = useCallback(async () => {
    const id = conversationIdRef.current
    if (!id) return

    const assistantId = localId('assistant')
    setMessages((current) => {
      // Drop the previous reply, mirroring what the server does. Leaving it in
      // place would show two answers to one question while only one exists.
      const withoutLastReply =
        current.at(-1)?.role === 'assistant' ? current.slice(0, -1) : current
      return [
        ...withoutLastReply,
        { id: assistantId, role: 'assistant', content: '', pending: true },
      ]
    })

    await consume(assistantId, (onEvent, signal) =>
      regenerateRequest({ conversationId: id, onEvent, signal }),
    )
  }, [consume])

  const lastMessage = messages.at(-1)
  const canRegenerate =
    !isStreaming &&
    conversationId !== null &&
    lastMessage?.role === 'assistant' &&
    !lastMessage.pending

  return {
    messages,
    conversationId,
    isStreaming,
    error,
    canRegenerate,
    send,
    regenerate,
    stop,
    load,
    reset,
  }
}
