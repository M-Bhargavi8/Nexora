/**
 * Uploads started from the chat composer.
 *
 * Separate from `useFiles` because the chat screen does not need the library:
 * it never lists or deletes files, it only needs to send one and say how that
 * went. Reusing `useFiles` here would fetch the whole file list on every visit
 * to the chat for nothing.
 *
 * The rules are shared, though -- `localRejectionReason` and `uploadFile` are
 * the same ones the Files page uses, so a file is accepted or refused
 * identically whichever button the person pressed.
 *
 * A finished upload is *staged*: the next message sends its id as
 * `attach_file_ids`, which adds it to that conversation so every question
 * there draws on it first. `staged` lists them; `clear` drops them once sent.
 */

import { useCallback, useState } from 'react'
import { ApiError } from '../lib/api'
import type { ConversationFileDto } from '../lib/chat'
import { uploadFile, type StoredFileDetailDto } from '../lib/files'
import { localRejectionReason } from './useFiles'

export type ChatUploadState = 'uploading' | 'ready' | 'no-text' | 'error'

export interface ChatUpload {
  /** Local id; the server's id is not known until the upload finishes. */
  id: string
  filename: string
  state: ChatUploadState
  progress: number
  /** Shown beside the filename when the upload did not end in `ready`. */
  message: string | null
  /** The stored file, once the server has it -- what gets attached. */
  file: ConversationFileDto | null
}

export interface UseChatUploads {
  uploads: ChatUpload[]
  isUploading: boolean
  /** Stored files waiting to be added to the conversation by the next send. */
  staged: ConversationFileDto[]
  /** Forget these uploads, once their files have been sent. */
  clear: (fileIds: string[]) => void
  upload: (files: FileList | File[]) => Promise<void>
  dismiss: (id: string) => void
}

let counter = 0
function nextId(): string {
  counter += 1
  return `chat-upload-${counter}`
}

/** What the person can do with the file now that it is stored. */
function settledState(stored: StoredFileDetailDto): Pick<ChatUpload, 'state' | 'message'> {
  if (stored.status === 'ready' && stored.has_text) {
    return {
      state: 'ready',
      message: stored.reused ? 'Already uploaded — using your existing copy' : null,
    }
  }
  if (stored.status === 'failed') {
    return { state: 'error', message: stored.error ?? 'Could not read this file.' }
  }
  // Stored, but there is nothing to search: an image, or a scan with no text.
  return {
    state: 'no-text',
    message: stored.error ?? 'Stored, but it has no readable text to ask about.',
  }
}

export function useChatUploads(): UseChatUploads {
  const [uploads, setUploads] = useState<ChatUpload[]>([])

  const update = useCallback((id: string, patch: Partial<ChatUpload>) => {
    setUploads((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    )
  }, [])

  const upload = useCallback(
    async (incoming: FileList | File[]) => {
      // Sequential, for the same reasons as the Files page: the server's
      // upload rate limit, and progress bars that mean something.
      for (const file of Array.from(incoming)) {
        const id = nextId()
        const rejection = localRejectionReason(file)

        setUploads((current) => [
          ...current,
          {
            id,
            filename: file.name,
            state: rejection ? 'error' : 'uploading',
            progress: 0,
            message: rejection,
            file: null,
          },
        ])
        if (rejection) continue

        try {
          const stored = await uploadFile(file, {
            onProgress: (fraction) => update(id, { progress: fraction }),
          })
          update(id, {
            progress: 1,
            ...settledState(stored),
            file: {
              id: stored.id,
              filename: stored.filename,
              status: stored.status,
              has_text: stored.has_text,
            },
          })
        } catch (caught) {
          update(id, {
            state: 'error',
            message: caught instanceof ApiError ? caught.message : 'Upload failed.',
          })
        }
      }
    },
    [update],
  )

  const dismiss = useCallback((id: string) => {
    setUploads((current) => current.filter((item) => item.id !== id))
  }, [])

  const clear = useCallback((fileIds: string[]) => {
    setUploads((current) =>
      current.filter((item) => !(item.file && fileIds.includes(item.file.id))),
    )
  }, [])

  return {
    uploads,
    isUploading: uploads.some((item) => item.state === 'uploading'),
    // A failed upload has no stored file, so it is never staged.
    staged: uploads.flatMap((item) => (item.file ? [item.file] : [])),
    clear,
    upload,
    dismiss,
  }
}
