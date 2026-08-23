/**
 * Tests for the sidebar's list state.
 *
 * The page-level tests in `ChatPage.test.tsx` cover what the user sees. These
 * cover the parts that are hard to reach through the UI -- notably the
 * stale-response race, which needs two overlapping requests resolving out of
 * order.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as chatApi from '../lib/chat'
import type { ConversationDto, ConversationPageDto } from '../lib/chat'
import { useConversations } from './useConversations'

vi.mock('../lib/chat', () => ({
  listConversations: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
}))

function conversation(id: string, title: string | null): ConversationDto {
  return { id, title, created_at: '', updated_at: '' }
}

function page(items: ConversationDto[], hasMore = false): ConversationPageDto {
  return { items, limit: 30, offset: 0, has_more: hasMore }
}

beforeEach(() => {
  vi.mocked(chatApi.listConversations).mockReset()
  vi.mocked(chatApi.renameConversation).mockReset()
  vi.mocked(chatApi.deleteConversation).mockReset()
  vi.mocked(chatApi.listConversations).mockResolvedValue(page([]))
})

describe('loading', () => {
  it('fetches the first page on mount', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'A thread')]),
    )

    const { result } = renderHook(() => useConversations())

    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
    expect(result.current.conversations[0].title).toBe('A thread')
  })

  it('survives the list failing', async () => {
    vi.mocked(chatApi.listConversations).mockRejectedValue(new Error('offline'))

    const { result } = renderHook(() => useConversations())

    // The sidebar is a convenience: losing it must not take the chat down.
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.conversations).toEqual([])
  })
})

describe('paging', () => {
  it('appends the next page instead of replacing the list', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'first')], true),
    )
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c2', 'second')], false),
    )
    await act(async () => {
      await result.current.loadMore()
    })

    expect(result.current.conversations.map((c) => c.title)).toEqual([
      'first',
      'second',
    ])
    expect(result.current.hasMore).toBe(false)
  })

  it('asks for the next page starting after what it already has', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'first'), conversation('c2', 'second')], true),
    )
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    await act(async () => {
      await result.current.loadMore()
    })

    expect(chatApi.listConversations).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 2 }),
    )
  })
})

describe('searching', () => {
  it('sends the term once typing settles', async () => {
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(chatApi.listConversations).toHaveBeenCalled())

    act(() => {
      result.current.setSearch('pgvector')
    })

    await waitFor(() =>
      expect(chatApi.listConversations).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: 'pgvector' }),
      ),
    )
  })

  it('replaces the list rather than appending search results', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'unrelated')]),
    )
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c2', 'pgvector notes')]),
    )
    act(() => {
      result.current.setSearch('pgvector')
    })

    await waitFor(() =>
      expect(result.current.conversations.map((c) => c.title)).toEqual([
        'pgvector notes',
      ]),
    )
  })

  it('ignores a slow response for a term that is no longer current', async () => {
    // The race that makes a search box show results for something you already
    // stopped typing: an earlier request resolving *after* a later one.
    const slow = page([conversation('stale', 'stale result')])
    const fast = page([conversation('fresh', 'fresh result')])

    let releaseSlow: (value: ConversationPageDto) => void = () => {}
    vi.mocked(chatApi.listConversations)
      .mockResolvedValueOnce(page([]))
      .mockImplementationOnce(
        () =>
          new Promise<ConversationPageDto>((resolve) => {
            releaseSlow = resolve
          }),
      )
      .mockResolvedValueOnce(fast)

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(chatApi.listConversations).toHaveBeenCalledTimes(1))

    act(() => {
      result.current.setSearch('a')
    })
    await waitFor(() => expect(chatApi.listConversations).toHaveBeenCalledTimes(2))

    act(() => {
      result.current.setSearch('ab')
    })
    await waitFor(() => expect(chatApi.listConversations).toHaveBeenCalledTimes(3))
    await waitFor(() =>
      expect(result.current.conversations.map((c) => c.title)).toEqual([
        'fresh result',
      ]),
    )

    // Now the earlier request finally answers. Its result is stale and must
    // not overwrite what is on screen.
    await act(async () => {
      releaseSlow(slow)
    })

    expect(result.current.conversations.map((c) => c.title)).toEqual([
      'fresh result',
    ])
  })
})

describe('renaming', () => {
  it('updates the row in place', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Old title')]),
    )
    vi.mocked(chatApi.renameConversation).mockResolvedValue(
      conversation('c1', 'New title'),
    )
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))

    await act(async () => {
      await result.current.rename('c1', 'New title')
    })

    expect(result.current.conversations[0].title).toBe('New title')
  })

  it('does not refetch the page', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Old title')]),
    )
    vi.mocked(chatApi.renameConversation).mockResolvedValue(
      conversation('c1', 'New title'),
    )
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
    const callsBefore = vi.mocked(chatApi.listConversations).mock.calls.length

    await act(async () => {
      await result.current.rename('c1', 'New title')
    })

    // Renaming does not reorder the list, so refetching would redraw the same
    // rows in the same order -- a flicker for nothing.
    expect(vi.mocked(chatApi.listConversations).mock.calls.length).toBe(
      callsBefore,
    )
  })
})

describe('deleting', () => {
  it('drops the row', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      page([conversation('c1', 'Doomed'), conversation('c2', 'Kept')]),
    )
    vi.mocked(chatApi.deleteConversation).mockResolvedValue(undefined)
    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toHaveLength(2))

    await act(async () => {
      await result.current.remove('c1')
    })

    expect(result.current.conversations.map((c) => c.title)).toEqual(['Kept'])
  })
})
