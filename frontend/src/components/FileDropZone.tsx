/** Drag-and-drop target that also works as an ordinary file picker. */

import { useState, type RefObject } from 'react'
import { ACCEPTED_EXTENSIONS, MAX_UPLOAD_BYTES } from '../lib/files'

interface FileDropZoneProps {
  onFiles: (files: FileList | File[]) => void
  inputRef: RefObject<HTMLInputElement | null>
}

export function FileDropZone({ onFiles, inputRef }: FileDropZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  // Drag events fire on every child element, so a plain boolean flickers as
  // the pointer crosses the inner text. Counting enter/leave pairs is what
  // makes the highlight stable.
  const [depth, setDepth] = useState(0)

  function handleDragEnter(event: React.DragEvent) {
    event.preventDefault()
    setDepth((current) => current + 1)
    setIsDragging(true)
  }

  function handleDragLeave(event: React.DragEvent) {
    event.preventDefault()
    setDepth((current) => {
      const next = current - 1
      if (next <= 0) setIsDragging(false)
      return Math.max(0, next)
    })
  }

  function handleDrop(event: React.DragEvent) {
    // Without preventDefault the browser navigates away to display the
    // dropped file, discarding the page.
    event.preventDefault()
    setDepth(0)
    setIsDragging(false)
    if (event.dataTransfer.files.length > 0) onFiles(event.dataTransfer.files)
  }

  return (
    <div
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
      className={`rounded-xl border border-dashed px-4 py-8 text-center transition-colors ${
        isDragging
          ? 'border-brand-500 bg-brand-600/10'
          : 'border-ink-600 bg-ink-900/40'
      }`}
    >
      <p className="text-sm text-mist-300">
        Drag files here, or{' '}
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="font-medium text-brand-300 underline underline-offset-4 hover:text-brand-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
        >
          browse
        </button>
      </p>
      <p className="mt-2 text-xs text-mist-500">
        {ACCEPTED_EXTENSIONS.join(', ')} · up to{' '}
        {MAX_UPLOAD_BYTES / (1024 * 1024)}MB
      </p>

      {/*
        The real input, kept out of view but not `display: none` -- a hidden
        input is still reachable by the file picker we open programmatically,
        while `display: none` would remove it from the accessibility tree
        entirely. `accept` is a convenience for the picker dialog and nothing
        more: it filters what the dialog shows and is trivially bypassed, which
        is why the server checks the bytes regardless.
      */}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXTENSIONS.join(',')}
        aria-label="Choose files to upload"
        onChange={(event) => {
          if (event.target.files) onFiles(event.target.files)
          // Reset, so choosing the same file twice in a row still fires
          // `change` the second time.
          event.target.value = ''
        }}
        className="sr-only"
      />
    </div>
  )
}
