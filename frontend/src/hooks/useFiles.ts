/**
 * State for the file library: what is stored, and what is currently uploading.
 *
 * Uploads are tracked separately from stored files because they are different
 * things. An upload in flight has progress and can fail; a stored file has an
 * id and can be opened. Merging them into one list would mean every consumer
 * checking which kind it is holding.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../lib/api'
import {
  ACCEPTED_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  deleteFile as deleteRequest,
  listFiles,
  uploadFile,
  type StoredFileDto,
} from '../lib/files'

export const PAGE_SIZE = 30

/** One upload in flight. `id` is local -- the server has not assigned one yet. */
export interface PendingUpload {
  id: string
  filename: string
  progress: number
  error: string | null
}

export interface UseFiles {
  files: StoredFileDto[]
  pending: PendingUpload[]
  isLoading: boolean
  hasMore: boolean
  error: string | null
  /** Informational, not an error: e.g. an upload matched an existing file. */
  notice: string | null
  upload: (files: FileList | File[]) => Promise<void>
  remove: (id: string) => Promise<void>
  loadMore: () => Promise<void>
  refresh: () => Promise<void>
  dismissError: () => void
}

let localCounter = 0
function localId(): string {
  localCounter += 1
  return `upload-${localCounter}`
}

/**
 * Reject what the server would reject, before spending the round trip.
 *
 * This is a courtesy, not a control. The server repeats every one of these
 * checks against the actual bytes, because anything enforced only in the
 * browser is enforced nowhere -- a client is trivially bypassed.
 *
 * Exported so the chat composer's upload button applies the same rules.
 */
export function localRejectionReason(file: File): string | null {
  if (file.size === 0) return 'This file is empty.'
  if (file.size > MAX_UPLOAD_BYTES) {
    return `This file is larger than ${MAX_UPLOAD_BYTES / (1024 * 1024)}MB.`
  }

  const dot = file.name.lastIndexOf('.')
  const extension = dot === -1 ? '' : file.name.slice(dot).toLowerCase()
  if (extension && !ACCEPTED_EXTENSIONS.includes(extension as never)) {
    return `${extension} files are not supported.`
  }
  return null
}

export function useFiles(): UseFiles {
  const [files, setFiles] = useState<StoredFileDto[]>([])
  const [pending, setPending] = useState<PendingUpload[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const requestRef = useRef(0)

  const fetchPage = useCallback(async (offset: number) => {
    const requestId = requestRef.current + 1
    requestRef.current = requestId
    setIsLoading(true)

    try {
      const page = await listFiles({ limit: PAGE_SIZE, offset })
      // A newer request started while this one was in flight; its answer is
      // the current one, so drop this stale response.
      if (requestRef.current !== requestId) return

      setFiles((current) => (offset === 0 ? page.items : [...current, ...page.items]))
      setHasMore(page.has_more)
    } catch {
      setError('Could not load your files.')
    } finally {
      if (requestRef.current === requestId) setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchPage(0)
  }, [fetchPage])

  const refresh = useCallback(() => fetchPage(0), [fetchPage])
  const loadMore = useCallback(
    () => fetchPage(files.length),
    [fetchPage, files.length],
  )

  const upload = useCallback(async (incoming: FileList | File[]) => {
    const chosen = Array.from(incoming)
    if (chosen.length === 0) return
    setError(null)
    setNotice(null)

    // Sequential rather than parallel. Ten files at once would race the
    // server's upload rate limit and, more importantly, saturate the
    // connection so that every individual progress bar crawls.
    for (const file of chosen) {
      const rejection = localRejectionReason(file)
      if (rejection) {
        setError(`${file.name}: ${rejection}`)
        continue
      }

      const id = localId()
      setPending((current) => [
        ...current,
        { id, filename: file.name, progress: 0, error: null },
      ])

      try {
        const stored = await uploadFile(file, {
          onProgress: (fraction) =>
            setPending((current) =>
              current.map((item) =>
                item.id === id ? { ...item, progress: fraction } : item,
              ),
            ),
        })
        if (stored.reused) {
          // The server kept the existing copy rather than storing another,
          // so there is no new row -- adding one would list the file twice.
          setNotice(`${file.name}: already uploaded as ${stored.filename}, so no copy was made.`)
        } else {
          // Newest first, matching the server's ordering, so the list does
          // not reorder itself on the next refresh.
          setFiles((current) => [stored, ...current])
        }
      } catch (caught) {
        setError(
          `${file.name}: ${
            caught instanceof ApiError ? caught.message : 'Upload failed.'
          }`,
        )
      } finally {
        setPending((current) => current.filter((item) => item.id !== id))
      }
    }
  }, [])

  const remove = useCallback(async (id: string) => {
    try {
      await deleteRequest(id)
      setFiles((current) => current.filter((file) => file.id !== id))
    } catch {
      setError('Could not delete that file.')
    }
  }, [])

  const dismissError = useCallback(() => {
    setError(null)
    setNotice(null)
  }, [])

  return {
    files,
    pending,
    isLoading,
    hasMore,
    error,
    notice,
    upload,
    remove,
    loadMore,
    refresh,
    dismissError,
  }
}
