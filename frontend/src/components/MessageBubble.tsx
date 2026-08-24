/** One turn in the conversation. */

import { useEffect, useRef, useState } from 'react'
import { CopyButton } from './CopyButton'
import { Markdown } from './Markdown'
import { SourceList } from './SourceList'
import type { DisplayMessage } from '../hooks/useChat'

interface MessageBubbleProps {
  message: DisplayMessage
  /** Absent while a reply is streaming, which is when editing is disallowed. */
  onEdit?: (messageId: string, content: string) => void
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

/** A user turn being rewritten in place. */
function EditableQuestion({
  message,
  onSave,
  onCancel,
}: {
  message: DisplayMessage
  onSave: (content: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(message.content)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.focus()
    // Cursor at the end rather than the start: an edit is usually a small
    // change to the end of what was typed, not a rewrite from the front.
    textarea.setSelectionRange(draft.length, draft.length)
    // Intentionally on mount only -- re-running would fight the user's cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const trimmed = draft.trim()
  const isUnchanged = trimmed === message.content.trim()

  return (
    <div className="flex justify-end">
      <div className="w-full max-w-[85%]">
        <textarea
          ref={textareaRef}
          value={draft}
          aria-label="Edit your message"
          rows={Math.min(10, draft.split('\n').length + 1)}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter saves, Shift+Enter adds a line -- the same contract the
            // composer uses, so the two do not behave differently.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (trimmed && !isUnchanged) onSave(trimmed)
            }
            if (event.key === 'Escape') onCancel()
          }}
          className="w-full resize-none rounded-2xl border border-brand-500/40 bg-ink-900 px-4 py-2.5 text-sm leading-relaxed text-mist-100 focus:outline focus:outline-2 focus:outline-offset-1 focus:outline-brand-500"
        />
        <div className="mt-1.5 flex items-center justify-end gap-2">
          <span className="mr-auto text-[11px] text-mist-600">
            Later turns will be replaced.
          </span>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md px-2.5 py-1 text-xs text-mist-400 hover:text-mist-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSave(trimmed)}
            disabled={!trimmed || isUnchanged}
            className="rounded-md border border-brand-500/40 bg-brand-600/20 px-2.5 py-1 text-xs font-medium text-brand-200 transition-colors hover:bg-brand-600/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {/* Not just "Send": the composer already has a button by that
                name, and two identically-labelled buttons on one screen are
                ambiguous to anyone navigating by label rather than position. */}
            Send edit
          </button>
        </div>
      </div>
    </div>
  )
}

export function MessageBubble({ message, onEdit }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  const [isEditing, setIsEditing] = useState(false)

  if (isUser) {
    // Only a message the server knows about can be edited -- see `persisted`
    // on DisplayMessage. A turn that never reached the server has no id to
    // name in the request.
    const canEdit = Boolean(onEdit) && message.persisted === true

    if (isEditing && onEdit) {
      return (
        <EditableQuestion
          message={message}
          onCancel={() => setIsEditing(false)}
          onSave={(content) => {
            setIsEditing(false)
            onEdit(message.id, content)
          }}
        />
      )
    }

    return (
      <div className="group flex flex-col items-end">
        {/* whitespace-pre-wrap, not markdown: what the user typed is shown
            exactly as typed. Rendering their input as markdown would mangle a
            question that happens to contain asterisks or underscores. */}
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600/15 px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap text-mist-100 ring-1 ring-inset ring-brand-500/20">
          {message.content}
        </div>

        {canEdit && (
          <div className="mt-1.5 flex opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <button
              type="button"
              onClick={() => setIsEditing(true)}
              className="rounded px-1.5 py-1 text-[11px] text-mist-500 transition-colors hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              Edit
            </button>
          </div>
        )}
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

        {/* Shown even while the reply streams: sources arrive before the
            first token, and seeing what NEXORA is reading is useful precisely
            while it is still answering. */}
        {message.sources && message.sources.length > 0 && (
          <SourceList sources={message.sources} />
        )}

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
