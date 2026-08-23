/** The scrolling transcript. */

import { useEffect, useRef } from 'react'
import { MessageBubble } from './MessageBubble'
import type { DisplayMessage } from '../hooks/useChat'

/** How close to the bottom still counts as "following along", in pixels. */
const FOLLOW_THRESHOLD = 120

interface MessageListProps {
  messages: DisplayMessage[]
  /** Omitted while streaming, which disables editing for the duration. */
  onEdit?: (messageId: string, content: string) => void
}

export function MessageList({ messages, onEdit }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Whether the user is still reading the latest text, as opposed to having
  // scrolled up to re-read something earlier.
  const isFollowingRef = useRef(true)

  // The last dependency is the length of the streaming reply, so this runs on
  // every chunk rather than only when a message is added.
  const streamedLength = messages.at(-1)?.content.length ?? 0

  useEffect(() => {
    const container = containerRef.current
    if (!container || !isFollowingRef.current) return
    container.scrollTop = container.scrollHeight
  }, [messages.length, streamedLength])

  function handleScroll() {
    const container = containerRef.current
    if (!container) return
    // Auto-scrolling unconditionally would drag the view back to the bottom on
    // every token, making it impossible to read anything earlier while a reply
    // is still arriving. Following stops the moment the user scrolls away and
    // resumes when they come back down.
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    isFollowingRef.current = distanceFromBottom <= FOLLOW_THRESHOLD
  }

  if (messages.length === 0) {
    return (
      <div
        ref={containerRef}
        className="flex flex-1 items-center justify-center overflow-y-auto px-4"
      >
        <div className="max-w-md text-center">
          <h2 className="bg-gradient-to-r from-brand-300 via-brand-400 to-halo-400 bg-clip-text text-2xl font-semibold text-transparent">
            NEXORA
          </h2>
          <p className="mt-2 text-sm text-mist-400">
            Ask a question to begin. Your conversations are private to your account.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4 py-6"
    >
      {/* aria-live announces new text to screen readers as it streams in.
          "polite" rather than "assertive" so it waits for a pause instead of
          interrupting on every token. */}
      <div className="mx-auto flex max-w-3xl flex-col gap-5" aria-live="polite">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} onEdit={onEdit} />
        ))}
      </div>
    </div>
  )
}
