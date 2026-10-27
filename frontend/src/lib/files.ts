/**
 * File API client.
 *
 * Uploads go out as `multipart/form-data`, not JSON. JSON has no binary type,
 * so sending a PDF through it means base64 -- a third larger, and the whole
 * file has to be held in memory to encode and again to decode. Multipart
 * streams, which is what lets the server enforce its size limit while the
 * bytes are still arriving.
 */

import { API_BASE_URL, API_PREFIX, ApiError, request } from './api'

export type FileStatus = 'ready' | 'extracted_empty' | 'unsupported' | 'failed'

export interface StoredFileDto {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  status: FileStatus
  page_count: number | null
  error: string | null
  created_at: string
  has_text: boolean
}

export interface StoredFileDetailDto extends StoredFileDto {
  extracted_text: string | null
  /** On upload: true when the bytes matched a file the user already had, and
   *  that existing file was returned instead of storing a copy. */
  reused?: boolean
}

export interface FilePageDto {
  items: StoredFileDto[]
  limit: number
  offset: number
  has_more: boolean
}

/** Extensions the backend accepts. Mirrors ACCEPTED_EXTENSIONS in upload.py. */
export const ACCEPTED_EXTENSIONS = [
  '.txt',
  '.md',
  '.csv',
  '.pdf',
  '.docx',
  '.xlsx',
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
] as const

/** Matches MAX_UPLOAD_BYTES on the server, for a message before the round trip. */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Upload one file.
 *
 * Uses XMLHttpRequest rather than fetch for one reason: progress. `fetch` has
 * no upload-progress event, and a 20MB upload with no feedback looks
 * indistinguishable from a frozen page. This is the rare case where the older
 * API does something the newer one still cannot.
 */
export function uploadFile(
  file: File,
  { onProgress, signal }: { onProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<StoredFileDetailDto> {
  return new Promise((resolve, reject) => {
    const body = new FormData()
    body.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE_URL}${API_PREFIX}/files`)
    // The session cookie is httpOnly, so it has to be sent by the browser
    // rather than attached by us -- exactly as `credentials: 'include'` does
    // for fetch.
    xhr.withCredentials = true

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total)
    })

    xhr.addEventListener('load', () => {
      let parsed: unknown = null
      try {
        parsed = JSON.parse(xhr.responseText)
      } catch {
        // Falls through to the status-based message below.
      }

      // 201 stored a new file; 200 means identical bytes were already stored
      // and that file came back instead. Both are success.
      if (xhr.status === 201 || xhr.status === 200) {
        resolve(parsed as StoredFileDetailDto)
        return
      }

      const detail =
        parsed && typeof parsed === 'object' && 'detail' in parsed
          ? (parsed as { detail: unknown }).detail
          : null
      reject(
        new ApiError(
          typeof detail === 'string' ? detail : `Upload failed (HTTP ${xhr.status})`,
          xhr.status,
        ),
      )
    })

    xhr.addEventListener('error', () =>
      reject(new ApiError('Could not reach the server.', 0)),
    )
    xhr.addEventListener('abort', () =>
      reject(new ApiError('Upload cancelled.', 0)),
    )

    signal?.addEventListener('abort', () => xhr.abort())
    xhr.send(body)
  })
}

export async function listFiles(
  { limit, offset }: { limit?: number; offset?: number } = {},
): Promise<FilePageDto> {
  const params = new URLSearchParams()
  if (limit !== undefined) params.set('limit', String(limit))
  if (offset !== undefined) params.set('offset', String(offset))
  const suffix = params.size > 0 ? `?${params}` : ''

  const { data } = await request<FilePageDto>(`/files${suffix}`)
  return data
}

export async function getFile(id: string): Promise<StoredFileDetailDto> {
  const { data } = await request<StoredFileDetailDto>(`/files/${id}`, {
    // Extracted text can be large, and the shared default is tuned for small
    // JSON responses.
    timeoutMs: 30_000,
  })
  return data
}

export async function deleteFile(id: string): Promise<void> {
  await request<void>(`/files/${id}`, { method: 'DELETE' })
}

/** The URL the browser should navigate to in order to download a file. */
export function downloadUrl(id: string): string {
  return `${API_BASE_URL}${API_PREFIX}/files/${id}/download`
}
