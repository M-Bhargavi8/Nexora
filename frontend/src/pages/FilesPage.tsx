/**
 * The file library: upload, inspect what was extracted, download, delete.
 *
 * Reaching this page at all means `RequireAuth` resolved a valid session.
 */

import { useRef, useState } from 'react'
import { Link } from 'react-router'
import { FileDropZone } from '../components/FileDropZone'
import { FileRow } from '../components/FileRow'
import { TextPreview } from '../components/TextPreview'
import { useAuth } from '../hooks/useAuth'
import { useFiles } from '../hooks/useFiles'

export default function FilesPage() {
  const { user } = useAuth()
  const library = useFiles()
  const [openFileId, setOpenFileId] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-ink-700/60 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Link
            to="/"
            className="bg-gradient-to-r from-brand-300 via-brand-400 to-halo-400 bg-clip-text text-sm font-semibold text-transparent"
          >
            NEXORA
          </Link>
          <span className="rounded border border-ink-700 bg-ink-850 px-1.5 py-0.5 text-[10px] font-medium text-mist-500">
            Files
          </span>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="text-xs text-mist-500 underline-offset-4 hover:text-mist-300 hover:underline"
          >
            Back to chat
          </Link>
          <span className="hidden max-w-[180px] truncate text-xs text-mist-500 sm:block">
            {user?.email}
          </span>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-3xl">
          <h1 className="text-lg font-semibold text-mist-100">Your files</h1>
          <p className="mt-1 text-sm text-mist-400">
            Upload documents and NEXORA will read the text out of them. Files
            are private to your account.
          </p>

          <div className="mt-5">
            <FileDropZone
              onFiles={(files) => void library.upload(files)}
              inputRef={inputRef}
            />
          </div>

          {library.error && (
            <div className="mt-4 flex items-start gap-3 rounded-lg border border-down-400/25 bg-down-600/10 px-3 py-2">
              <p role="alert" className="flex-1 text-xs text-down-400">
                {library.error}
              </p>
              <button
                type="button"
                onClick={library.dismissError}
                className="text-xs text-mist-500 hover:text-mist-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
              >
                Dismiss
              </button>
            </div>
          )}

          {library.pending.length > 0 && (
            <ul className="mt-4 space-y-2">
              {library.pending.map((upload) => (
                <li
                  key={upload.id}
                  className="rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2.5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate text-sm text-mist-200">
                      {upload.filename}
                    </span>
                    <span className="shrink-0 text-xs text-mist-500">
                      {Math.round(upload.progress * 100)}%
                    </span>
                  </div>
                  <div
                    className="mt-2 h-1 overflow-hidden rounded-full bg-ink-700"
                    role="progressbar"
                    aria-label={`Uploading ${upload.filename}`}
                    aria-valuenow={Math.round(upload.progress * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <div
                      className="h-full rounded-full bg-brand-500 transition-[width]"
                      style={{ width: `${upload.progress * 100}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}

          <section className="mt-6" aria-label="Stored files">
            {library.files.length === 0 && library.pending.length === 0 ? (
              <p className="rounded-lg border border-dashed border-ink-700 px-4 py-8 text-center text-sm text-mist-500">
                {library.isLoading ? 'Loading…' : 'No files yet.'}
              </p>
            ) : (
              <ul className="space-y-2">
                {library.files.map((file) => (
                  <FileRow
                    key={file.id}
                    file={file}
                    isOpen={openFileId === file.id}
                    onToggle={() =>
                      setOpenFileId(openFileId === file.id ? null : file.id)
                    }
                    onDelete={() => void library.remove(file.id)}
                  />
                ))}
              </ul>
            )}

            {library.hasMore && (
              <button
                type="button"
                onClick={() => void library.loadMore()}
                disabled={library.isLoading}
                className="mt-3 w-full rounded-lg border border-ink-700 px-3 py-2 text-xs text-mist-400 transition-colors hover:border-brand-500/40 hover:text-mist-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:opacity-50"
              >
                {library.isLoading ? 'Loading…' : 'Load more'}
              </button>
            )}
          </section>

          {openFileId && <TextPreview fileId={openFileId} />}
        </div>
      </div>
    </div>
  )
}
