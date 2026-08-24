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
import type { ChatEvent, ConversationDto } from '../lib/chat'
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
  editMessage: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
}))

const ADA = {
  id: '11111111-1111-1111-1111-111111111111',
  email: 'ada@example.com',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

/** Build a conversation-list page around the given items. */
function page(
  items: ConversationDto[],
  hasMore = false,
): chatApi.ConversationPageDto {
  return { items, limit: 30, offset: 0, has_more: hasMore }
}

function conversation(id: string, title: string | null): ConversationDto {
  return { id, title, created_at: '', updated_at: '' }
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
  vi.mocked(chatApi.listConversations).mockResolvedValue(page([]))
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
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'How do embeddings work?')]),
    )

    renderChatPage()

    expect(
      await screen.findByRole('button', { name: 'How do embeddings work?' }),
    ).toBeInTheDocument()
  })

  it('labels an untitled conversation rather than showing nothing', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', null)]),
    )

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

    // Re-queried inside waitFor rather than holding the node `findByText`
    // returned. Regenerating gives the reply a new local id, so React unmounts
    // the old bubble and mounts a new one -- and the sidebar refresh that runs
    // when streaming stops triggers another render on top. A node captured
    // before either can be detached by the time the assertion reads it, which
    // fails with a confusing "element could not be found in the document".
    await waitFor(() =>
      expect(screen.getByText('A different answer')).toBeInTheDocument(),
    )
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

describe('renaming a conversation', () => {
  beforeEach(() => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Old title')]),
    )
  })

  it('saves the new title on Enter', async () => {
    vi.mocked(chatApi.renameConversation).mockResolvedValue(
      conversation('c1', 'New title'),
    )
    renderChatPage()
    await userEvent.click(await screen.findByRole('button', { name: /^Rename/ }))

    const input = screen.getByLabelText('Conversation title')
    await userEvent.clear(input)
    await userEvent.type(input, 'New title{Enter}')

    await waitFor(() =>
      expect(chatApi.renameConversation).toHaveBeenCalledWith('c1', 'New title'),
    )
    expect(
      await screen.findByRole('button', { name: 'New title' }),
    ).toBeInTheDocument()
  })

  it('abandons the edit on Escape', async () => {
    renderChatPage()
    await userEvent.click(await screen.findByRole('button', { name: /^Rename/ }))

    const input = screen.getByLabelText('Conversation title')
    await userEvent.clear(input)
    await userEvent.type(input, 'Discarded{Escape}')

    // Escape is the only way out of a rename you did not mean to start.
    expect(chatApi.renameConversation).not.toHaveBeenCalled()
    expect(
      await screen.findByRole('button', { name: 'Old title' }),
    ).toBeInTheDocument()
  })

  it('does not send an unchanged title', async () => {
    renderChatPage()
    await userEvent.click(await screen.findByRole('button', { name: /^Rename/ }))

    await userEvent.type(screen.getByLabelText('Conversation title'), '{Enter}')

    expect(chatApi.renameConversation).not.toHaveBeenCalled()
  })

  it('does not send a blank title', async () => {
    renderChatPage()
    await userEvent.click(await screen.findByRole('button', { name: /^Rename/ }))

    const input = screen.getByLabelText('Conversation title')
    await userEvent.clear(input)
    await userEvent.type(input, '   {Enter}')

    expect(chatApi.renameConversation).not.toHaveBeenCalled()
  })
})

describe('deleting a conversation', () => {
  beforeEach(() => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Doomed thread')]),
    )
    vi.mocked(chatApi.deleteConversation).mockResolvedValue(undefined)
  })

  it('asks for confirmation before deleting', async () => {
    renderChatPage()

    await userEvent.click(await screen.findByRole('button', { name: /^Delete/ }))

    // Deleting is irreversible, so one stray click must not do it.
    expect(chatApi.deleteConversation).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
  })

  it('deletes once confirmed and drops the row', async () => {
    renderChatPage()
    await userEvent.click(await screen.findByRole('button', { name: /^Delete/ }))

    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() =>
      expect(chatApi.deleteConversation).toHaveBeenCalledWith('c1'),
    )
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Doomed thread' }),
      ).not.toBeInTheDocument(),
    )
  })

  it('can be cancelled', async () => {
    renderChatPage()
    await userEvent.click(await screen.findByRole('button', { name: /^Delete/ }))

    await userEvent.click(screen.getByRole('button', { name: 'Cancel deleting' }))

    expect(chatApi.deleteConversation).not.toHaveBeenCalled()
    expect(
      screen.getByRole('button', { name: 'Doomed thread' }),
    ).toBeInTheDocument()
  })

  it('clears the transcript when the open conversation is deleted', async () => {
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      id: 'c1',
      title: 'Doomed thread',
      created_at: '',
      updated_at: '',
      messages: [
        {
          id: 'm1',
          role: 'user',
          content: 'a question in the open thread',
          position: 0,
          created_at: '',
          model: null,
          input_tokens: null,
          output_tokens: null,
        },
      ],
    })
    renderChatPage()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Doomed thread' }),
    )
    await screen.findByText('a question in the open thread')

    await userEvent.click(screen.getByRole('button', { name: /^Delete/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    // Leaving the messages on screen would offer a composer that answers 404.
    await waitFor(() =>
      expect(
        screen.queryByText('a question in the open thread'),
      ).not.toBeInTheDocument(),
    )
  })
})

describe('searching conversations', () => {
  it('queries the API with the typed term', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Postgres tuning')]),
    )
    renderChatPage()

    await userEvent.type(
      await screen.findByLabelText('Search conversations'),
      'postgres',
    )

    await waitFor(() =>
      expect(chatApi.listConversations).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: 'postgres' }),
      ),
    )
  })

  it('debounces so typing does not fire a request per keystroke', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(page([]))
    renderChatPage()
    await screen.findByLabelText('Search conversations')
    vi.mocked(chatApi.listConversations).mockClear()

    await userEvent.type(
      screen.getByLabelText('Search conversations'),
      'pgvector',
    )

    // Eight characters must not mean eight requests, seven of them already
    // stale by the time they land.
    await waitFor(() =>
      expect(chatApi.listConversations).toHaveBeenCalledWith(
        expect.objectContaining({ query: 'pgvector' }),
      ),
    )
    expect(
      vi.mocked(chatApi.listConversations).mock.calls.length,
    ).toBeLessThan(8)
  })

  it('says so when nothing matches', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(page([]))
    renderChatPage()

    await userEvent.type(
      await screen.findByLabelText('Search conversations'),
      'nothing',
    )

    expect(
      await screen.findByText('No conversations match that search.'),
    ).toBeInTheDocument()
  })
})

describe('paging the conversation list', () => {
  it('offers Load more only when another page exists', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Only thread')], false),
    )
    renderChatPage()
    await screen.findByRole('button', { name: 'Only thread' })

    expect(
      screen.queryByRole('button', { name: 'Load more' }),
    ).not.toBeInTheDocument()
  })

  it('appends the next page rather than replacing the list', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'First page thread')], true),
    )
    renderChatPage()
    await screen.findByRole('button', { name: 'First page thread' })

    vi.mocked(chatApi.listConversations).mockResolvedValue({
      items: [conversation('c2', 'Second page thread')],
      limit: 30,
      offset: 1,
      has_more: false,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    expect(
      await screen.findByRole('button', { name: 'Second page thread' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'First page thread' }),
    ).toBeInTheDocument()
  })

  it('asks for the next page by offset', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'First page thread')], true),
    )
    renderChatPage()
    await screen.findByRole('button', { name: 'First page thread' })

    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    await waitFor(() =>
      expect(chatApi.listConversations).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 1 }),
      ),
    )
  })
})

describe('editing a question', () => {
  async function sendOnce() {
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'orginal question{Enter}')
    await screen.findByText('Hello there')
  }

  it('offers no edit control on the assistant reply', async () => {
    await sendOnce()

    // Exactly one Edit button: the user's turn, not the reply. Rewriting a
    // reply would let the user put words in the model's mouth.
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1)
  })

  it('sends the edit with the id the server gave the message', async () => {
    vi.mocked(chatApi.editMessage).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'delta', text: 'A better answer' })
      onEvent({
        type: 'done',
        conversation_id: 'c1',
        message_id: 'm2',
        model: 'echo',
        input_tokens: null,
        output_tokens: null,
      })
    })
    await sendOnce()

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const textarea = screen.getByLabelText('Edit your message')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'original question{Enter}')

    // 'u1' is the id from the start event, not the local placeholder -- naming
    // a local id would reference a row the server has never heard of.
    await waitFor(() =>
      expect(chatApi.editMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          conversationId: 'c1',
          messageId: 'u1',
          content: 'original question',
        }),
      ),
    )
  })

  it('replaces the old question and its answer on screen', async () => {
    vi.mocked(chatApi.editMessage).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'delta', text: 'A better answer' })
      onEvent({
        type: 'done',
        conversation_id: 'c1',
        message_id: 'm2',
        model: 'echo',
        input_tokens: null,
        output_tokens: null,
      })
    })
    await sendOnce()

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const textarea = screen.getByLabelText('Edit your message')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'original question{Enter}')

    expect(await screen.findByText('A better answer')).toBeInTheDocument()
    // The old reply answered wording that is no longer above it.
    expect(screen.queryByText('Hello there')).not.toBeInTheDocument()
    expect(screen.queryByText('orginal question')).not.toBeInTheDocument()
  })

  it('can be cancelled without sending anything', async () => {
    await sendOnce()

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(chatApi.editMessage).not.toHaveBeenCalled()
    expect(screen.getByText('orginal question')).toBeInTheDocument()
  })

  it('will not send an unchanged question', async () => {
    await sendOnce()

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))

    // Nothing changed, so there is nothing to re-ask -- and re-asking would
    // still cost a full model call.
    expect(screen.getByRole('button', { name: 'Send edit' })).toBeDisabled()
  })
})

describe('showing sources', () => {
  /** Drive a reply that reports retrieved passages. */
  function respondWithSources(sources: chatApi.Source[]) {
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' })
      onEvent({ type: 'sources', sources })
      onEvent({ type: 'delta', text: 'Roughly four kilowatts.' })
      onEvent({
        type: 'done',
        conversation_id: 'c1',
        message_id: 'm1',
        model: 'echo',
        input_tokens: null,
        output_tokens: null,
      })
    })
  }

  const SOLAR_SOURCE: chatApi.Source = {
    index: 1,
    file_id: 'f1',
    filename: 'solar.txt',
    chunk_id: 'ch1',
    position: 0,
    similarity: 0.82,
    preview: 'A residential solar installation typically produces four kilowatts.',
  }

  it('says how many passages were used', async () => {
    respondWithSources([SOLAR_SOURCE])
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'how much power?{Enter}')

    expect(
      await screen.findByText(/Answered using 1 passage from your files/),
    ).toBeInTheDocument()
  })

  it('pluralises correctly', async () => {
    respondWithSources([SOLAR_SOURCE, { ...SOLAR_SOURCE, index: 2, chunk_id: 'ch2' }])
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'how much power?{Enter}')

    expect(
      await screen.findByText(/Answered using 2 passages from your files/),
    ).toBeInTheDocument()
  })

  it('reveals the passage that was actually read', async () => {
    respondWithSources([SOLAR_SOURCE])
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'how much power?{Enter}')
    await screen.findByText(/Answered using 1 passage/)

    await userEvent.click(screen.getByRole('button', { name: /Answered using/ }))

    // An answer citing a document is only checkable if the passage is visible.
    expect(await screen.findByText(/produces four kilowatts/)).toBeInTheDocument()
    expect(screen.getByText('solar.txt')).toBeInTheDocument()
    expect(screen.getByText('[Source 1]')).toBeInTheDocument()
    expect(screen.getByText('82% match')).toBeInTheDocument()
  })

  it('shows nothing when no documents were used', async () => {
    // The default mock streams no `sources` event at all.
    renderChatPage()
    const composer = await screen.findByLabelText('Message')

    await userEvent.type(composer, 'a general question{Enter}')
    await screen.findByText('Hello there')

    // Silence is the honest signal: nothing was read, so nothing is claimed.
    expect(screen.queryByText(/Answered using/)).not.toBeInTheDocument()
  })

  it('renders a passage as text, never as markup', async () => {
    respondWithSources([
      { ...SOLAR_SOURCE, preview: '<img src=x onerror="alert(1)">' },
    ])
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'question{Enter}')
    await screen.findByText(/Answered using 1 passage/)

    await userEvent.click(screen.getByRole('button', { name: /Answered using/ }))

    // SECURITY: this text came out of a file NEXORA did not write. Rendering
    // it as markup would undo the upload validation at the very last step.
    const shown = await screen.findByText('<img src=x onerror="alert(1)">')
    expect(shown.querySelector('img')).toBeNull()
  })

  it('keeps sources attached to the reply they informed', async () => {
    respondWithSources([SOLAR_SOURCE])
    renderChatPage()
    const composer = await screen.findByLabelText('Message')
    await userEvent.type(composer, 'first question{Enter}')
    await screen.findByText(/Answered using 1 passage/)

    // A second turn that retrieves nothing.
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'start', conversation_id: 'c1', user_message_id: 'u2' })
      onEvent({ type: 'delta', text: 'No documents needed.' })
      onEvent({
        type: 'done',
        conversation_id: 'c1',
        message_id: 'm2',
        model: 'echo',
        input_tokens: null,
        output_tokens: null,
      })
    })
    await userEvent.type(composer, 'second question{Enter}')
    await screen.findByText('No documents needed.')

    // Scrolling back must still show which answer was grounded in a document.
    expect(screen.getAllByText(/Answered using/)).toHaveLength(1)
  })
})
