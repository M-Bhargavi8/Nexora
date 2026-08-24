/** One stored file in the library list. */

import { useState } from 'react'
import { downloadUrl, formatBytes, type StoredFileDto } from '../lib/files'

interface FileRowProps {
  file: StoredFileDto
  isOpen: boolean
  onToggle: () => void
  onDelete: () => void
}

/** How each status reads to the person who uploaded the file. */
const STATUS_LABEL: Record<StoredFileDto['status'], string> = {
  ready: 'Text extracted',
  extracted_empty: 'No text found',
  unsupported: 'Stored, not read',
  failed: 'Could not read',
}

const STATUS_STYLE: Record<StoredFileDto['status'], string> = {
  ready: 'border-ok-400/30 bg-ok-600/10 text-ok-400',
  // Neither good nor bad: the file is stored, there is simply nothing to read.
  extracted_empty: 'border-warn-400/30 bg-ink-800 text-warn-400',
  unsupported: 'border-ink-600 bg-ink-800 text-mist-500',
  failed: 'border-down-400/30 bg-down-600/10 text-down-400',
}

export function FileRow({ file, isOpen, onToggle, onDelete }: FileRowProps) {
  // Deleting is irreversible, so it takes two clicks -- the same rule the
  // conversation list follows.
  const [isConfirming, setIsConfirming] = useState(false)

  return (
    <li className="rounded-lg border border-ink-700 bg-ink-900/60">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm text-mist-100">{file.filename}</span>
            <span
              className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                STATUS_STYLE[file.status]
              }`}
            >
              {STATUS_LABEL[file.status]}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-mist-500">
            {formatBytes(file.size_bytes)}
            {file.page_count !== null && ` · ${file.page_count} pages`}
            {file.error && ` · ${file.error}`}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {file.has_text && (
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={isOpen}
              className="rounded px-1.5 py-1 text-[11px] text-mist-400 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              {isOpen ? 'Hide text' : 'View text'}
            </button>
          )}

          {/*
            A plain link, not fetch-then-blob. The browser handles the
            download, and the server's Content-Disposition header is what
            makes it save rather than render -- which is the control that
            stops an uploaded page from executing on our origin.
          */}
          <a
            href={downloadUrl(file.id)}
            className="rounded px-1.5 py-1 text-[11px] text-mist-400 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
          >
            Download
          </a>

          {isConfirming ? (
            <>
              <button
                type="button"
                onClick={onDelete}
                className="rounded px-1.5 py-1 text-[11px] font-medium text-down-400 hover:bg-down-600/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
              >
                Confirm
              </button>
              <button
                type="button"
                onClick={() => setIsConfirming(false)}
                aria-label={`Cancel deleting ${file.filename}`}
                className="rounded px-1.5 py-1 text-[11px] text-mist-500 hover:text-mist-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setIsConfirming(true)}
              aria-label={`Delete ${file.filename}`}
              className="rounded px-1.5 py-1 text-[11px] text-mist-500 hover:text-down-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              Delete
            </button>
          )}
        </div>
      </div>
    </li>
  )
}
