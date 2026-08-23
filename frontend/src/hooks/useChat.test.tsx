/**
 * Tests for the conversation state machine.
 *
 * The chat API module is mocked, so nothing here opens a socket.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../lib/api'
import * as chatApi from '../lib/chat'
import type { ChatEvent } from '../lib/chat'
import { useChat } from './useChat'

vi.mock('../lib/chat', () => ({
  streamChat: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
}))

/** Drive `streamChat` by replaying a fixed script of events. */
function respondWith(events: ChatEvent[]) {
  vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent }) => {
    for (const event of events) onEvent(event)
  })
}

const START: ChatEvent = {
  type: 'start',
  conversation_id: 'c1',
  user_message_id: 'u1',
}
const DONE: ChatEvent = {
  type: 'done',
  conversation_id: 'c1',
  message_id: 'm1',
  model: 'echo',
  input_tokens: null,
  output_tokens: null,
}

beforeEach(() => {
  vi.mocked(chatApi.streamChat).mockReset()
  vi.mocked(chatApi.getConversation).mockReset()
})

describe('sending a message', () => {
  it('shows the user turn and the reply', async () => {
    respondWith([START, { type: 'delta', text: 'Hello ' }, { type: 'delta', text: 'world' }, DONE])
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi there')
    })

    expect(result.current.messages.map((m) => [m.role, m.content])).toEqual([
      ['user', 'hi there'],
      ['assistant', 'Hello world'],
    ])
  })

  it('appends each delta rather than replacing the text', async () => {
    // Each event carries one fragment; no fragment is meaningful alone.
    respondWith([
      START,
      { type: 'delta', text: 'a' },
      { type: 'delta', text: 'b' },
      { type: 'delta', text: 'c' },
      DONE,
    ])
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.messages.at(-1)?.content).toBe('abc')
  })

  it('learns the conversation id from the start event', async () => {
    respondWith([START, DONE])
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.conversationId).toBe('c1')
  })

  it('sends the conversation id on the second message', async () => {
    respondWith([START, DONE])
    const { result } = renderHook(() => useChat())
    await act(async () => {
      await result.current.send('first')
    })

    await act(async () => {
      await result.current.send('second')
    })

    // The first call starts a new conversation, the second continues it. This
    // is what the ref exists for -- reading state in the callback would still
    // see null on the second send.
    expect(vi.mocked(chatApi.streamChat).mock.calls[0][0].conversationId).toBeNull()
    expect(vi.mocked(chatApi.streamChat).mock.calls[1][0].conversationId).toBe('c1')
  })

  it('ignores an empty message', async () => {
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('   ')
    })

    expect(chatApi.streamChat).not.toHaveBeenCalled()
    expect(result.current.messages).toEqual([])
  })

  it('is not streaming once the reply completes', async () => {
    respondWith([START, { type: 'delta', text: 'x' }, DONE])
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.messages.at(-1)?.pending).toBeFalsy()
  })
})

describe('failures', () => {
  it('surfaces an error event and marks the turn as unfinished', async () => {
    respondWith([
      START,
      { type: 'delta', text: 'partial' },
      { type: 'error', code: 'provider_unavailable', message: 'Unavailable.' },
    ])
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.error).toBe('Unavailable.')
    const reply = result.current.messages.at(-1)
    // The partial text stays on screen -- the server already saved it, so
    // hiding it would show the user less than actually exists.
    expect(reply?.content).toBe('partial')
    expect(reply?.failed).toBe(true)
  })

  it('reports a request that never started', async () => {
    vi.mocked(chatApi.streamChat).mockRejectedValue(
      new ApiError('Too many messages.', 429),
    )
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.error).toBe('Too many messages.')
  })

  it('does not leak an internal error message to the user', async () => {
    vi.mocked(chatApi.streamChat).mockRejectedValue(new Error('ECONNREFUSED 127.0.0.1'))
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.error).toBe('Could not reach the assistant.')
  })

  it('stops streaming even when the request fails', async () => {
    vi.mocked(chatApi.streamChat).mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    expect(result.current.isStreaming).toBe(false)
  })
})

describe('stopping', () => {
  it('keeps the text that already arrived', async () => {
    vi.mocked(chatApi.streamChat).mockImplementation(async ({ onEvent, signal }) => {
      onEvent(START)
      onEvent({ type: 'delta', text: 'half an answer' })
      // What fetch does when the AbortController fires.
      const error = new Error('aborted')
      error.name = 'AbortError'
      if (signal) Object.defineProperty(signal, 'aborted', { value: true })
      throw error
    })
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send('hi')
    })

    const reply = result.current.messages.at(-1)
    expect(reply?.content).toBe('half an answer')
    // Stopping is a choice, not a failure -- it must not be flagged as one.
    expect(reply?.failed).toBeFalsy()
    expect(result.current.error).toBeNull()
  })
})

describe('loading an existing conversation', () => {
  it('replaces the transcript with the stored messages', async () => {
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      id: 'c9',
      title: 'Old chat',
      created_at: '',
      updated_at: '',
      messages: [
        {
          id: 'm1',
          role: 'user',
          content: 'earlier question',
          position: 0,
          created_at: '',
          model: null,
          input_tokens: null,
          output_tokens: null,
        },
        {
          id: 'm2',
          role: 'assistant',
          content: 'earlier answer',
          position: 1,
          created_at: '',
          model: 'echo',
          input_tokens: null,
          output_tokens: null,
        },
      ],
    })
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.load('c9')
    })

    expect(result.current.conversationId).toBe('c9')
    expect(result.current.messages.map((m) => m.content)).toEqual([
      'earlier question',
      'earlier answer',
    ])
  })

  it('explains a conversation that is gone', async () => {
    vi.mocked(chatApi.getConversation).mockRejectedValue(new ApiError('nope', 404))
    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.load('missing')
    })

    expect(result.current.error).toBe('That conversation no longer exists.')
  })
})

describe('starting over', () => {
  it('clears the transcript and the conversation id', async () => {
    respondWith([START, { type: 'delta', text: 'x' }, DONE])
    const { result } = renderHook(() => useChat())
    await act(async () => {
      await result.current.send('hi')
    })

    act(() => {
      result.current.reset()
    })

    expect(result.current.messages).toEqual([])
    expect(result.current.conversationId).toBeNull()
  })

  it('sends no conversation id after a reset', async () => {
    respondWith([START, DONE])
    const { result } = renderHook(() => useChat())
    await act(async () => {
      await result.current.send('first')
    })

    act(() => {
      result.current.reset()
    })
    await act(async () => {
      await result.current.send('fresh start')
    })

    expect(vi.mocked(chatApi.streamChat).mock.calls[1][0].conversationId).toBeNull()
  })
})
