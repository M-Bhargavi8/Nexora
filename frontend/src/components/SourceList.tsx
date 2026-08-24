/**
 * The passages NEXORA read from your documents to answer a question.
 *
 * This is the part of retrieval the user can actually check. An answer that
 * cites a document is only trustworthy if you can see *which* passage it came
 * from -- otherwise "according to your files" is just a more confident way of
 * guessing. So these come from the server's retrieval result, never from
 * anything the model wrote, and each one can be expanded to read the text that
 * was actually supplied.
 */

import { useState } from 'react'
import { Link } from 'react-router'
import type { Source } from '../lib/chat'

interface SourceListProps {
  sources: Source[]
}

export function SourceList({ sources }: SourceListProps) {
  const [isOpen, setIsOpen] = useState(false)

  if (sources.length === 0) return null

  return (
    <section
      aria-label="Sources"
      className="mt-2 rounded-lg border border-ink-700/70 bg-ink-950/50"
    >
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[11px] text-mist-400 transition-colors hover:text-mist-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
      >
        <span>
          Answered using {sources.length}{' '}
          {sources.length === 1 ? 'passage' : 'passages'} from your files
        </span>
        <span aria-hidden="true" className="text-mist-600">
          {isOpen ? '▾' : '▸'}
        </span>
      </button>

      {isOpen && (
        <ol className="space-y-2 border-t border-ink-700/70 px-3 py-2">
          {sources.map((source) => (
            <li key={source.chunk_id} className="text-[11px]">
              <div className="flex items-baseline gap-2">
                {/* The label matches the [Source N] marker the model was
                    shown, so a citation in the reply points at a real row. */}
                <span className="shrink-0 font-medium text-brand-300">
                  [Source {source.index}]
                </span>
                <Link
                  to="/files"
                  className="truncate text-mist-300 underline-offset-4 hover:text-mist-100 hover:underline"
                >
                  {source.filename}
                </Link>
                <span
                  className="ml-auto shrink-0 text-mist-600"
                  title="How closely this passage matched the question"
                >
                  {Math.round(source.similarity * 100)}% match
                </span>
              </div>
              {/* whitespace-pre-wrap, never markdown: this is text from a file
                  NEXORA did not write. Rendering it as markup would undo the
                  upload validation at the last step. */}
              <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-mist-500">
                {source.preview}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
