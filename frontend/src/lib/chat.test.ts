/**
 * Tests for the SSE reader.
 *
 * Network chunk boundaries have nothing to do with event boundaries, so most
 * of these deliberately split the byte stream in awkward places. That is not
 * a contrived edge case -- it is what TCP actually does.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { streamChat, type ChatEvent } from './chat'

const encoder = new TextEncoder()

function sse(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`
}

/** A response body that delivers exactly these byte chunks, in order. */
function bodyOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
}

function mockFetch(chunks: Uint8Array[], init: { ok?: boolean; status?: number } = {}) {
  const response = {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    body: bodyOf(chunks),
    json: async () => ({}),
  }
  const fetchMock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Runs a stream against whatever `mockFetch` last installed. */
async function collect(): Promise<ChatEvent[]> {
  const events: ChatEvent[] = []
  await streamChat({ content: 'hello', onEvent: (e) => events.push(e) })
  return events
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('event framing', () => {
  it('reads several events delivered in a single chunk', async () => {
    mockFetch([
      encoder.encode(
        sse({ type: 'start', conversation_id: 'c1', user_message_id: 'u1' }) +
          sse({ type: 'delta', text: 'Hello ' }) +
          sse({ type: 'delta', text: 'world' }),
      ),
    ])

    const events = await collect()

    expect(events.map((e) => e.type)).toEqual(['start', 'delta', 'delta'])
  })

  it('reassembles one event split across two chunks', async () => {
    const whole = sse({ type: 'delta', text: 'Hello world' })
    const bytes = encoder.encode(whole)
    const cut = 12 // mid-JSON, nowhere near an event boundary

    mockFetch([bytes.slice(0, cut), bytes.slice(cut)])

    const events = await collect()

    expect(events).toEqual([{ type: 'delta', text: 'Hello world' }])
  })

  it('does not emit an event until its terminator arrives', async () => {
    // The first chunk holds a complete event plus the beginning of a second.
    const first = sse({ type: 'delta', text: 'one' }) + 'data: {"type":"delta",'
    const second = '"text":"two"}\n\n'

    mockFetch([encoder.encode(first), encoder.encode(second)])

    const events = await collect()

    expect(events).toEqual([
      { type: 'delta', text: 'one' },
      { type: 'delta', text: 'two' },
    ])
  })

  it('keeps a multi-byte character intact when it is split across chunks', async () => {
    // é is two bytes in UTF-8. Decoding each chunk independently would turn it
    // into replacement characters -- which is what happens without
    // `{ stream: true }` on the decoder.
    const bytes = encoder.encode(sse({ type: 'delta', text: 'café' }))
    const eAcuteStart = bytes.indexOf(0xc3)
    expect(eAcuteStart).toBeGreaterThan(-1)

    mockFetch([bytes.slice(0, eAcuteStart + 1), bytes.slice(eAcuteStart + 1)])

    const events = await collect()

    expect(events).toEqual([{ type: 'delta', text: 'café' }])
  })

  it('survives a malformed event without losing the rest', async () => {
    mockFetch([
      encoder.encode(
        sse({ type: 'delta', text: 'before' }) +
          'data: {not json at all}\n\n' +
          sse({ type: 'delta', text: 'after' }),
      ),
    ])

    const events = await collect()

    expect(events).toEqual([
      { type: 'delta', text: 'before' },
      { type: 'delta', text: 'after' },
    ])
  })

  it('ignores lines that are not data fields', async () => {
    mockFetch([
      encoder.encode(': a comment\n\n' + sse({ type: 'delta', text: 'real' })),
    ])

    const events = await collect()

    expect(events).toEqual([{ type: 'delta', text: 'real' }])
  })
})

describe('failures before the stream opens', () => {
  it('raises the server message for a rejected request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        json: async () => ({ detail: 'Too many messages.' }),
      }),
    )

    await expect(
      streamChat({ content: 'hello', onEvent: () => {} }),
    ).rejects.toThrow('Too many messages.')
  })

  it('carries the status code so the caller can tell 401 from 429', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Not authenticated' }),
      }),
    )

    await expect(
      streamChat({ content: 'hello', onEvent: () => {} }),
    ).rejects.toMatchObject({ status: 401 })
  })

  it('falls back to the status when the error body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('not json')
        },
      }),
    )

    await expect(
      streamChat({ content: 'hello', onEvent: () => {} }),
    ).rejects.toBeInstanceOf(ApiError)
  })
})

describe('request shape', () => {
  it('sends the session cookie', async () => {
    const fetchMock = mockFetch([encoder.encode(sse({ type: 'delta', text: 'x' }))])

    await streamChat({ content: 'hello', onEvent: () => {} })

    // Without credentials: 'include' the httpOnly cookie is never attached and
    // every request is anonymous.
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'include' })
  })

  it('omits conversation_id when starting a new conversation', async () => {
    const fetchMock = mockFetch([encoder.encode(sse({ type: 'delta', text: 'x' }))])

    await streamChat({ content: 'hello', onEvent: () => {} })

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body).toEqual({ content: 'hello' })
  })

  it('includes conversation_id when continuing one', async () => {
    const fetchMock = mockFetch([encoder.encode(sse({ type: 'delta', text: 'x' }))])

    await streamChat({ content: 'hello', conversationId: 'c1', onEvent: () => {} })

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body).toEqual({ content: 'hello', conversation_id: 'c1' })
  })
})
