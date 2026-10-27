/** The message input. */

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { ACCEPTED_EXTENSIONS } from '../lib/files'

/** Mirrors MAX_MESSAGE_LENGTH in app/schemas/chat.py. */
const MAX_MESSAGE_LENGTH = 32_000

interface ComposerProps {
  onSend: (content: string) => void
  onStop: () => void
  isStreaming: boolean
  disabled?: boolean
  /** Shows an upload button beside the input when given. */
  onUpload?: (files: File[]) => void
  /** Holds Send until uploads finish, so a question never outruns its file. */
  isUploading?: boolean
}

export function Composer({
  onSend,
  onStop,
  isStreaming,
  disabled,
  onUpload,
  isUploading,
}: ComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Grow with the content instead of scrolling inside a fixed box, up to a
  // ceiling so a pasted essay cannot push the conversation off screen.
  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
  }, [value])

  const tooLong = value.length > MAX_MESSAGE_LENGTH
  const canSend =
    value.trim().length > 0 && !tooLong && !isStreaming && !disabled && !isUploading

  function submit() {
    if (!canSend) return
    onSend(value)
    setValue('')
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    submit()
  }

  function handleFilesChosen(event: ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(event.target.files ?? [])
    // Cleared so that choosing the same file again still fires a change --
    // otherwise retrying a failed upload silently does nothing.
    event.target.value = ''
    if (chosen.length > 0) onUpload?.(chosen)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter makes a newline -- the convention every chat
    // application uses, so doing anything else here surprises people.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border-t border-ink-700/60 bg-ink-900/70 p-3 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        {onUpload && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPTED_EXTENSIONS.join(',')}
              onChange={handleFilesChosen}
              className="hidden"
              data-testid="composer-file-input"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              aria-label="Upload a file"
              title="Upload a file (PDF, DOCX, XLSX, CSV, TXT, MD, images)"
              className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl border border-ink-600 bg-ink-800 text-mist-300 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {/* Paperclip. */}
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
          </>
        )}

        <label htmlFor="composer" className="sr-only">
          Message
        </label>
        <textarea
          id="composer"
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Ask NEXORA…"
          aria-invalid={tooLong || undefined}
          aria-describedby={tooLong ? 'composer-error' : undefined}
          className="max-h-[200px] min-h-[42px] flex-1 resize-none rounded-xl border border-ink-600 bg-ink-850 px-3.5 py-2.5 text-sm text-mist-100 placeholder:text-mist-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />

        {isStreaming ? (
          <button
            type="button"
            onClick={onStop}
            className="h-[42px] shrink-0 rounded-xl border border-ink-600 bg-ink-800 px-4 text-sm font-medium text-mist-200 transition-colors hover:border-down-400/50 hover:text-down-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSend}
            title={isUploading ? 'Waiting for your upload to finish' : undefined}
            className="h-[42px] shrink-0 rounded-xl bg-brand-600 px-4 text-sm font-medium text-white transition-colors hover:bg-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Send
          </button>
        )}
      </div>

      {tooLong && (
        <p
          id="composer-error"
          role="alert"
          className="mx-auto mt-2 max-w-3xl text-xs text-down-400"
        >
          That message is {(value.length - MAX_MESSAGE_LENGTH).toLocaleString()}{' '}
          characters too long.
        </p>
      )}
    </form>
  )
}
