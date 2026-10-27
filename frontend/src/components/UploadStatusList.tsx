/** One line per file uploaded from the chat composer, showing how it went. */

import type { ChatUpload } from '../hooks/useChatUploads'

interface UploadStatusListProps {
  uploads: ChatUpload[]
  onDismiss: (id: string) => void
}

const STATE_STYLE: Record<ChatUpload['state'], string> = {
  uploading: 'border-ink-600 bg-ink-850 text-mist-300',
  ready: 'border-ok-400/30 bg-ok-600/10 text-ok-400',
  'no-text': 'border-warn-400/30 bg-ink-850 text-warn-400',
  error: 'border-down-400/30 bg-down-600/10 text-down-400',
}

function describe(upload: ChatUpload): string {
  switch (upload.state) {
    case 'uploading':
      return `Uploading ${Math.round(upload.progress * 100)}%`
    case 'ready':
      return upload.message ?? 'Ready — added with your next message'
    default:
      return upload.message ?? ''
  }
}

export function UploadStatusList({ uploads, onDismiss }: UploadStatusListProps) {
  if (uploads.length === 0) return null

  return (
    // Announced politely: progress changes often, and an assertive region
    // would interrupt the person on every percent.
    <ul aria-live="polite" aria-label="Uploads" className="mx-auto mb-2 flex max-w-3xl flex-wrap gap-2">
      {uploads.map((upload) => (
        <li
          key={upload.id}
          className={`flex max-w-full items-center gap-2 rounded-lg border px-2.5 py-1 text-xs ${
            STATE_STYLE[upload.state]
          }`}
        >
          <span className="max-w-[200px] truncate font-medium text-mist-100">
            {upload.filename}
          </span>
          <span className="truncate">{describe(upload)}</span>
          {/* Nothing to dismiss mid-upload: the request keeps going, and
              hiding it would only make its result impossible to see. */}
          {upload.state !== 'uploading' && (
            <button
              type="button"
              onClick={() => onDismiss(upload.id)}
              aria-label={`Dismiss ${upload.filename}`}
              className="shrink-0 rounded px-1 text-mist-500 hover:text-mist-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              ×
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}
