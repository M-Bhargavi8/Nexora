/**
 * Page-level tests for the file library.
 *
 * The chat API module is mocked, so nothing here opens a socket or touches a
 * real file.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import * as filesApi from '../lib/files'
import type { StoredFileDetailDto, StoredFileDto } from '../lib/files'
import { AuthProvider } from '../contexts/AuthProvider'
import FilesPage from './FilesPage'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof api>()
  return { ...actual, getCurrentUser: vi.fn(), logout: vi.fn() }
})

vi.mock('../lib/files', async (importOriginal) => {
  const actual = await importOriginal<typeof filesApi>()
  return {
    // The constants and formatters are real -- only the network calls are
    // replaced, so the tests exercise the same limits the app uses.
    ...actual,
    uploadFile: vi.fn(),
    listFiles: vi.fn(),
    getFile: vi.fn(),
    deleteFile: vi.fn(),
  }
})

const ADA = {
  id: '11111111-1111-1111-1111-111111111111',
  email: 'ada@example.com',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

function storedFile(overrides: Partial<StoredFileDto> = {}): StoredFileDto {
  return {
    id: 'f1',
    filename: 'notes.txt',
    content_type: 'text/plain',
    size_bytes: 2048,
    status: 'ready',
    page_count: null,
    error: null,
    created_at: '2026-08-24T10:00:00Z',
    has_text: true,
    ...overrides,
  }
}

function page(items: StoredFileDto[], hasMore = false): filesApi.FilePageDto {
  return { items, limit: 30, offset: 0, has_more: hasMore }
}

function renderFilesPage() {
  return render(
    <MemoryRouter initialEntries={['/files']}>
      <AuthProvider>
        <FilesPage />
      </AuthProvider>
    </MemoryRouter>,
  )
}

/** A File object backed by real bytes, as the browser would supply. */
function makeFile(name: string, content = 'hello', type = 'text/plain'): File {
  return new File([content], name, { type })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)
  vi.mocked(filesApi.listFiles).mockResolvedValue(page([]))
})

describe('listing', () => {
  it('shows an empty state when there are no files', async () => {
    renderFilesPage()

    expect(await screen.findByText('No files yet.')).toBeInTheDocument()
  })

  it('lists stored files with their size', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(
      page([storedFile({ filename: 'report.pdf', size_bytes: 2048 })]),
    )

    renderFilesPage()

    expect(await screen.findByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText(/2 KB/)).toBeInTheDocument()
  })

  it('explains each extraction outcome rather than showing a raw status', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(
      page([
        storedFile({ id: 'a', filename: 'good.txt', status: 'ready' }),
        storedFile({
          id: 'b',
          filename: 'scan.pdf',
          status: 'extracted_empty',
          has_text: false,
        }),
        storedFile({
          id: 'c',
          filename: 'chart.png',
          status: 'unsupported',
          has_text: false,
        }),
        storedFile({
          id: 'd',
          filename: 'broken.docx',
          status: 'failed',
          has_text: false,
        }),
      ]),
    )

    renderFilesPage()

    expect(await screen.findByText('Text extracted')).toBeInTheDocument()
    expect(screen.getByText('No text found')).toBeInTheDocument()
    expect(screen.getByText('Stored, not read')).toBeInTheDocument()
    expect(screen.getByText('Could not read')).toBeInTheDocument()
  })

  it('offers View text only when there is text to view', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(
      page([
        storedFile({ id: 'a', filename: 'good.txt', has_text: true }),
        storedFile({ id: 'b', filename: 'chart.png', has_text: false }),
      ]),
    )

    renderFilesPage()
    await screen.findByText('good.txt')

    expect(screen.getAllByRole('button', { name: 'View text' })).toHaveLength(1)
  })

  it('appends the next page rather than replacing the list', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(
      page([storedFile({ id: 'a', filename: 'first.txt' })], true),
    )
    renderFilesPage()
    await screen.findByText('first.txt')

    vi.mocked(filesApi.listFiles).mockResolvedValue({
      items: [storedFile({ id: 'b', filename: 'second.txt' })],
      limit: 30,
      offset: 1,
      has_more: false,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByText('second.txt')).toBeInTheDocument()
    expect(screen.getByText('first.txt')).toBeInTheDocument()
  })
})

describe('uploading', () => {
  it('does not list a file twice when the upload matched an existing one', async () => {
    const existing = storedFile({ id: 'f-existing', filename: 'report.pdf' })
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([existing]))
    vi.mocked(filesApi.uploadFile).mockResolvedValue({
      ...existing,
      extracted_text: 'text',
      reused: true,
    })
    renderFilesPage()
    const input = await screen.findByLabelText('Choose files to upload')
    await screen.findByText('report.pdf')

    await userEvent.upload(input, makeFile('report copy.pdf'))

    expect(
      await screen.findByText(/already uploaded as report.pdf, so no copy was made/),
    ).toBeInTheDocument()
    expect(screen.getAllByText('report.pdf')).toHaveLength(1)
  })

  it('sends the chosen file and shows it in the list', async () => {
    const uploaded: StoredFileDetailDto = {
      ...storedFile({ filename: 'essay.txt' }),
      extracted_text: 'the text',
    }
    vi.mocked(filesApi.uploadFile).mockResolvedValue(uploaded)
    renderFilesPage()
    const input = await screen.findByLabelText('Choose files to upload')

    await userEvent.upload(input, makeFile('essay.txt'))

    await waitFor(() => expect(filesApi.uploadFile).toHaveBeenCalled())
    expect(await screen.findByText('essay.txt')).toBeInTheDocument()
  })

  it('uploads several files one after another', async () => {
    vi.mocked(filesApi.uploadFile).mockImplementation(async (file) => ({
      ...storedFile({ id: file.name, filename: file.name }),
      extracted_text: null,
    }))
    renderFilesPage()
    const input = await screen.findByLabelText('Choose files to upload')

    await userEvent.upload(input, [makeFile('one.txt'), makeFile('two.txt')])

    await waitFor(() => expect(filesApi.uploadFile).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('one.txt')).toBeInTheDocument()
    expect(screen.getByText('two.txt')).toBeInTheDocument()
  })

  it('reports a rejection from the server', async () => {
    vi.mocked(filesApi.uploadFile).mockRejectedValue(
      new api.ApiError('This file type is not supported.', 400),
    )
    renderFilesPage()
    const input = await screen.findByLabelText('Choose files to upload')

    await userEvent.upload(input, makeFile('notes.txt'))

    expect(
      await screen.findByText(/This file type is not supported\./),
    ).toBeInTheDocument()
  })

  it('rejects an oversized file without a round trip', async () => {
    renderFilesPage()
    const input = await screen.findByLabelText('Choose files to upload')
    const huge = makeFile('huge.txt', 'x')
    // Real bytes would make the test slow; the size is what is being checked.
    Object.defineProperty(huge, 'size', { value: 25 * 1024 * 1024 })

    await userEvent.upload(input, huge)

    expect(await screen.findByText(/larger than 20MB/)).toBeInTheDocument()
    // A courtesy check only -- the server repeats it, because anything
    // enforced solely in the browser is enforced nowhere.
    expect(filesApi.uploadFile).not.toHaveBeenCalled()
  })

  it('rejects an unsupported extension dropped onto the page', async () => {
    // Deliberately a *drop*, not the file picker. The input's `accept`
    // attribute filters what the picker offers, but drag-and-drop ignores it
    // completely -- so this is the path by which a disallowed file actually
    // arrives, and the one the check has to cover.
    renderFilesPage()
    const zone = (await screen.findByText(/Drag files here/)).closest('div')!

    fireEvent.drop(zone, {
      dataTransfer: {
        files: [makeFile('setup.exe', 'MZ', 'application/octet-stream')],
      },
    })

    expect(
      await screen.findByText(/\.exe files are not supported/),
    ).toBeInTheDocument()
    expect(filesApi.uploadFile).not.toHaveBeenCalled()
  })

  it('uploads a file dropped onto the page', async () => {
    vi.mocked(filesApi.uploadFile).mockResolvedValue({
      ...storedFile({ filename: 'dropped.txt' }),
      extracted_text: 'text',
    })
    renderFilesPage()
    const zone = (await screen.findByText(/Drag files here/)).closest('div')!

    fireEvent.drop(zone, {
      dataTransfer: { files: [makeFile('dropped.txt')] },
    })

    await waitFor(() => expect(filesApi.uploadFile).toHaveBeenCalled())
    expect(await screen.findByText('dropped.txt')).toBeInTheDocument()
  })

  it('lets the error be dismissed', async () => {
    vi.mocked(filesApi.uploadFile).mockRejectedValue(
      new api.ApiError('Upload failed.', 400),
    )
    renderFilesPage()
    const input = await screen.findByLabelText('Choose files to upload')
    await userEvent.upload(input, makeFile('notes.txt'))
    await screen.findByText(/Upload failed\./)

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(screen.queryByText(/Upload failed\./)).not.toBeInTheDocument()
  })
})

describe('viewing extracted text', () => {
  it('loads and shows the text on demand', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(
      page([storedFile({ filename: 'notes.txt' })]),
    )
    vi.mocked(filesApi.getFile).mockResolvedValue({
      ...storedFile({ filename: 'notes.txt' }),
      extracted_text: 'The quick brown fox.',
    })
    renderFilesPage()
    await screen.findByText('notes.txt')

    await userEvent.click(screen.getByRole('button', { name: 'View text' }))

    expect(await screen.findByText('The quick brown fox.')).toBeInTheDocument()
  })

  it('does not fetch the text until it is asked for', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([storedFile()]))
    renderFilesPage()
    await screen.findByText('notes.txt')

    // The full text of a large document has no business in a list payload.
    expect(filesApi.getFile).not.toHaveBeenCalled()
  })

  it('renders extracted text as plain text, never as markup', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([storedFile()]))
    vi.mocked(filesApi.getFile).mockResolvedValue({
      ...storedFile(),
      extracted_text: '<img src=x onerror="alert(1)">',
    })
    renderFilesPage()
    await screen.findByText('notes.txt')

    await userEvent.click(screen.getByRole('button', { name: 'View text' }))

    // SECURITY: the text came out of a file NEXORA did not create. It must
    // appear as characters, not become an element.
    const shown = await screen.findByText('<img src=x onerror="alert(1)">')
    expect(shown.querySelector('img')).toBeNull()
  })
})

describe('deleting', () => {
  it('asks for confirmation first', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([storedFile()]))
    renderFilesPage()
    await screen.findByText('notes.txt')

    await userEvent.click(screen.getByRole('button', { name: /^Delete/ }))

    expect(filesApi.deleteFile).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
  })

  it('deletes once confirmed and drops the row', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([storedFile()]))
    vi.mocked(filesApi.deleteFile).mockResolvedValue(undefined)
    renderFilesPage()
    await screen.findByText('notes.txt')
    await userEvent.click(screen.getByRole('button', { name: /^Delete/ }))

    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(filesApi.deleteFile).toHaveBeenCalledWith('f1'))
    await waitFor(() =>
      expect(screen.queryByText('notes.txt')).not.toBeInTheDocument(),
    )
  })

  it('can be cancelled', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([storedFile()]))
    renderFilesPage()
    await screen.findByText('notes.txt')
    await userEvent.click(screen.getByRole('button', { name: /^Delete/ }))

    await userEvent.click(
      screen.getByRole('button', { name: 'Cancel deleting notes.txt' }),
    )

    expect(filesApi.deleteFile).not.toHaveBeenCalled()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
  })
})

describe('downloading', () => {
  it('links straight at the download endpoint', async () => {
    vi.mocked(filesApi.listFiles).mockResolvedValue(page([storedFile()]))
    renderFilesPage()
    await screen.findByText('notes.txt')

    const link = screen.getByRole('link', { name: 'Download' })

    // A plain link, so the browser handles it and the server's
    // Content-Disposition header decides that it saves rather than renders.
    expect(link).toHaveAttribute('href', expect.stringContaining('/files/f1/download'))
  })
})
