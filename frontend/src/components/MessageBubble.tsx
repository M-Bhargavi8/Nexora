/** One turn in the conversation. */

import { CopyButton } from './CopyButton'
import { Markdown } from './Markdown'
import type { DisplayMessage } from '../hooks/useChat'

interface MessageBubbleProps {
  message: DisplayMessage
}

/** Three animated dots, shown before the first token arrives. */
function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 py-1" aria-label="Generating">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          aria-hidden="true"
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-mist-500"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  )
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end">
        {/* whitespace-pre-wrap, not markdown: what the user typed is shown
            exactly as typed. Rendering their input as markdown would mangle a
            question that happens to contain asterisks or underscores. */}
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600/15 px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap text-mist-100 ring-1 ring-inset ring-brand-500/20">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="group flex justify-start">
      <div className="w-full max-w-[92%]">
        <div className="rounded-2xl rounded-bl-md border border-ink-700/70 bg-ink-900/60 px-4 py-3">
          {message.content ? (
            <Markdown>{message.content}</Markdown>
          ) : message.pending ? (
            <TypingIndicator />
          ) : (
            <p className="text-sm text-mist-500 italic">No response.</p>
          )}

          {message.failed && (
            <p
              role="alert"
              className="mt-2 border-t border-down-400/20 pt-2 text-xs text-down-400"
            >
              This reply did not finish.
            </p>
          )}
        </div>

        {/* Only once the reply is complete -- a copy button on half an answer
            copies half an answer. Revealed on hover or keyboard focus, so it
            stays reachable without a mouse. */}
        {!message.pending && message.content && (
          <div className="mt-1.5 flex opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <CopyButton text={message.content} label="Copy" />
          </div>
        )}
      </div>
    </div>
  )
}
