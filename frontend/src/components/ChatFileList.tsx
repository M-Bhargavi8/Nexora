/** The files a conversation answers from first, shown above the composer. */

import type { ConversationFileDto } from '../lib/chat'

interface ChatFileListProps {
  files: ConversationFileDto[]
  onRemove: (fileId: string) => void
}

export function ChatFileList({ files, onRemove }: ChatFileListProps) {
  if (files.length === 0) return null

  return (
    <div className="mx-auto mb-2 flex max-w-3xl flex-wrap items-center gap-2">
      <span className="text-xs text-mist-500">In this chat:</span>
      <ul aria-label="Files in this chat" className="contents">
        {files.map((file) => (
          <li
            key={file.id}
            className="flex max-w-full items-center gap-1.5 rounded-lg border border-brand-500/30 bg-brand-600/10 px-2.5 py-1 text-xs text-mist-100"
          >
            <span className="max-w-[220px] truncate">{file.filename}</span>
            {!file.has_text && (
              // Stored but unreadable: an image with no reader configured, or a
              // scan. Said here so its silence in answers is not a mystery.
              <span className="text-warn-400">· no readable text</span>
            )}
            <button
              type="button"
              onClick={() => onRemove(file.id)}
              aria-label={`Remove ${file.filename} from this chat`}
              title="Remove from this chat (the file stays in your library)"
              className="shrink-0 rounded px-1 text-mist-500 hover:text-mist-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
