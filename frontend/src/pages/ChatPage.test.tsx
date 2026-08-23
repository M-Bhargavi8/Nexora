/**
 * Page-level tests for the chat workspace.
 *
 * These exist because of what went wrong in Phase 2: the auth pages were
 * written, complete and correct, and nothing imported them -- so none of it
 * was reachable and every unit test still passed. A test that actually renders
 * the page is what catches that.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import * as chatApi from '../lib/chat'
import type { ChatEvent } from '../lib/chat'
import { AuthProvider } from '../contexts/AuthProvider'
import ChatPage from './ChatPage'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof api>()
  return {
    ...actual,
    getCurrentUser: vi.fn(),
    login: vi.fn(),
    signup: vi.fn(),
    logout: vi.fn(),
  }
})

vi.mock('../lib/chat', () => ({
  streamChat: vi.fn(),
  regenerate: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
}))

const ADA = {
  id: '11111111-1111-1111-1111-111111111111',
  email: 'ada@example.com',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

function renderChatPage() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  // Explicit, because `restoreMocks` only restores vi.spyOn spies -- the
  // vi.fn()s created inside a vi.mock factory keep their call history across
  // tests, so an assertion like "was not called" silently measures every
  // previous test in the file instead of this one.
  vi.clearAllMocks()
  vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)
  vi.mocked(chatApi.listConversations).mockResolvedValue([])
  vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
    onEvent({
      type: 'start',
      conversation_id: 'c1',
      user_message_id: 'u1',
    } as ChatEvent)
    onEvent({ type: 'delta', text: 'Hello there' })
    onEvent({
      type: 'done',
      conversation_id: 'c1',
      message_id: 'm1',
      model: 'echo',
      input_tokens: null,
      output_tokens: null,
    })
  })
})

describe('rendering', () => {
  it('shows the composer and an empty state', async () => {
    renderChatPage()

    expect(await screen.findByLabelText('Message')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument()
    expect(screen.getByText(/Ask a question to begin/)).toBeInTheDocument()
  })

  it('shows who is signed in and a way out', async () => {
    renderChatPage()

    expect(await screen.findByText('ada@example.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('lists previous conversations', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue([
      { id: 'c1', title: 'How do embeddings work?', created_at: '', updated_at: '' },
    ])

    renderChatPage()

    expect(
      await screen.findByRole('button', { name: 'How do embeddings work?' }),
    ).toBeInTheDocument()
  })

  it('labels an untitled conversation rather than showing nothing', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue([
      { id: 'c1', title: null, created_at: '', updated_at: '' },
    ])

    renderChatPage()

    expect(
      await screen.findByRole('button', { name: 'New conversation' }),
    ).toBeInTheDocument()
  })
})

describe('sending', () => {
  it('shows the question and the streamed reply', async () => {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'what is a token?')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('what is a token?')).toBeInTheDocument()
    expect(await screen.findByText('Hello there')).toBeInTheDocument()
  })

  it('clears the composer after sending', async () => {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'hello')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(composer).toHaveValue(''))
  })

  it('sends on Enter', async () => {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'hello{Enter}')

    await waitFor(() => expect(chatApi.streamChat).toHaveBeenCalled())
  })

  it('does not send on Shift+Enter', async () => {
    // Shift+Enter must insert a newline. Sending here would make it impossible
    // to write a multi-line message.
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'line one{Shift>}{Enter}{/Shift}line two')

    expect(chatApi.streamChat).not.toHaveBeenCalled()
    expect(composer).toHaveValue('line one\nline two')
  })

  it('cannot send an empty message', async () => {
    renderChatPage()
    await screen.findByLabelText('Message')

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('shows an error when the assistant fails', async () => {
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({
        type: 'error',
        code: 'provider_unavailable',
        message: 'The assistant is unavailable right now.',
      })
    })
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'hello{Enter}')

    expect(
      await screen.findByText('The assistant is unavailable right now.'),
    ).toBeInTheDocument()
  })
})

describe('starting a new chat', () => {
  it('clears the transcript', async () => {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'first question{Enter}')
    await screen.findByText('first question')

    await userEvent.click(screen.getByRole('button', { name: 'New chat' }))

    await waitFor(() =>
      expect(screen.queryByText('first question')).not.toBeInTheDocument(),
    )
    expect(screen.getByText(/Ask a question to begin/)).toBeInTheDocument()
  })
})

describe('regenerating', () => {
  it('offers no regenerate button before anything has been asked', async () => {
    renderChatPage()
    await screen.findByLabelText('Message')

    expect(
      screen.queryByRole('button', { name: 'Regenerate reply' }),
    ).not.toBeInTheDocument()
  })

  it('offers it once a reply has completed', async () => {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'a question{Enter}')

    expect(
      await screen.findByRole('button', { name: 'Regenerate reply' }),
    ).toBeInTheDocument()
  })

  it('replaces the answer on screen', async () => {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'a question{Enter}')
    await screen.findByText('Hello there')

    vi.mocked(chatApi.regenerate).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'delta', text: 'A different answer' })
      onEvent({
        type: 'done',
        conversation_id: 'c1',
        message_id: 'm2',
        model: 'echo',
        input_tokens: null,
        output_tokens: null,
      })
    })
    await userEvent.click(
      screen.getByRole('button', { name: 'Regenerate reply' }),
    )

    expect(await screen.findByText('A different answer')).toBeInTheDocument()
    // Two answers to one question would misrepresent what is stored.
    expect(screen.queryByText('Hello there')).not.toBeInTheDocument()
    expect(screen.getByText('a question')).toBeInTheDocument()
  })
})

describe('stopping', () => {
  it('swaps Send for Stop while a reply is streaming', async () => {
    // A stream that never settles, so the UI stays in its streaming state.
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'delta', text: 'thinking…' })
      await new Promise(() => {})
    })
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'a question{Enter}')

    expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument()
  })

  it('aborts the request when Stop is pressed', async () => {
    let capturedSignal: AbortSignal | undefined
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent, signal }) => {
      capturedSignal = signal
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'delta', text: 'partial' })
      await new Promise(() => {})
    })
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'a question{Enter}')

    await userEvent.click(await screen.findByRole('button', { name: 'Stop' }))

    // The signal is what actually closes the connection; without it the
    // server keeps generating -- and keeps billing.
    expect(capturedSignal?.aborted).toBe(true)
  })

  it('keeps the partial text after stopping', async () => {
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'delta', text: 'half an answer' })
      await new Promise(() => {})
    })
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'a question{Enter}')
    await screen.findByText('half an answer')

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))

    expect(screen.getByText('half an answer')).toBeInTheDocument()
  })
})
