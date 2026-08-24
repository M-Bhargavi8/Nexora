/** Shows the text NEXORA extracted from one file. */

import { useEffect, useState } from 'react'
import { getFile, type StoredFileDetailDto } from '../lib/files'

interface TextPreviewProps {
  fileId: string
}

export function TextPreview({ fileId }: TextPreviewProps) {
  const [file, setFile] = useState<StoredFileDetailDto | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setFile(null)
    setError(null)

    void (async () => {
      try {
        const loaded = await getFile(fileId)
        // The user opened a different file while this was in flight. Applying
        // it now would show one file's text under another file's name.
        if (!cancelled) setFile(loaded)
      } catch {
        if (!cancelled) setError('Could not load the extracted text.')
      }
    })()

    return () => {
      cancelled = true
    }
  }, [fileId])

  return (
    <section className="mt-4 rounded-lg border border-ink-700 bg-ink-950/60">
      <header className="border-b border-ink-700/70 px-3 py-2">
        <h2 className="text-xs font-medium text-mist-300">
          {file ? `Extracted from ${file.filename}` : 'Extracted text'}
        </h2>
      </header>

      <div className="max-h-96 overflow-auto px-3 py-3">
        {error ? (
          <p role="alert" className="text-xs text-down-400">
            {error}
          </p>
        ) : file === null ? (
          <p className="text-xs text-mist-500">Loading…</p>
        ) : (
          // whitespace-pre-wrap, never markdown or HTML: this is text from a
          // file NEXORA did not create. Rendering it as markup would be a
          // stored-XSS hole opened at the last step, after the upload
          // validation went to the trouble of closing it.
          <pre className="text-xs leading-relaxed whitespace-pre-wrap text-mist-300">
            {file.extracted_text ?? 'No text was extracted from this file.'}
          </pre>
        )}
      </div>
    </section>
  )
}
