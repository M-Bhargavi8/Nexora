/**
 * The sidebar's state: which conversations to show, the search term, and how
 * many pages have been loaded.
 *
 * Kept out of `ChatPage` because it is genuinely separate from the transcript.
 * The page renders both; neither needs to know how the other works.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteConversation as deleteRequest,
  listConversations,
  renameConversation as renameRequest,
  type ConversationDto,
} from '../lib/chat'

/** How many conversations one page holds. */
export const PAGE_SIZE = 30

/**
 * How long to wait after the last keystroke before searching.
 *
 * Without this every character types a request, so "pgvector" fires eight --
 * seven of them already stale by the time they land. 250ms is below what
 * reads as lag while still collapsing normal typing into a single call.
 */
export const SEARCH_DEBOUNCE_MS = 250

export interface UseConversations {
  conversations: ConversationDto[]
  /** The term currently in the search box, updated on every keystroke. */
  search: string
  setSearch: (term: string) => void
  isLoading: boolean
  hasMore: boolean
  loadMore: () => Promise<void>
  refresh: () => Promise<void>
  rename: (id: string, title: string) => Promise<void>
  remove: (id: string) => Promise<void>
}

export function useConversations(): UseConversations {
  const [conversations, setConversations] = useState<ConversationDto[]>([])
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [hasMore, setHasMore] = useState(false)

  // Identifies the newest request. A slow response for an earlier search term
  // must not overwrite the results of a later one -- the classic race that
  // makes a search box show results for something you already stopped typing.
  const requestRef = useRef(0)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  const fetchPage = useCallback(
    async (offset: number, term: string) => {
      const requestId = requestRef.current + 1
      requestRef.current = requestId
      setIsLoading(true)

      try {
        const page = await listConversations({
          query: term || undefined,
          limit: PAGE_SIZE,
          offset,
        })

        // A newer request started while this one was in flight. Its answer is
        // the current one; this response is stale, so drop it.
        if (requestRef.current !== requestId) return

        setConversations((current) =>
          offset === 0 ? page.items : [...current, ...page.items],
        )
        setHasMore(page.has_more)
      } catch {
        // The sidebar is a convenience. Failing to load it must not take the
        // chat itself down -- a conversation is still perfectly usable
        // without a list of previous ones beside it.
      } finally {
        if (requestRef.current === requestId) setIsLoading(false)
      }
    },
    [],
  )

  // Re-runs whenever the settled search term changes, which also covers the
  // first load (both terms start empty).
  useEffect(() => {
    void fetchPage(0, debouncedSearch)
  }, [debouncedSearch, fetchPage])

  const refresh = useCallback(
    () => fetchPage(0, debouncedSearch),
    [fetchPage, debouncedSearch],
  )

  const loadMore = useCallback(
    () => fetchPage(conversations.length, debouncedSearch),
    [fetchPage, conversations.length, debouncedSearch],
  )

  const rename = useCallback(async (id: string, title: string) => {
    const updated = await renameRequest(id, title)
    // Patched in place rather than refetched: renaming does not reorder the
    // list, so re-reading the page would produce the same rows in the same
    // order and a visible flicker for nothing.
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === id ? { ...conversation, title: updated.title } : conversation,
      ),
    )
  }, [])

  const remove = useCallback(async (id: string) => {
    await deleteRequest(id)
    setConversations((current) =>
      current.filter((conversation) => conversation.id !== id),
    )
  }, [])

  return {
    conversations,
    search,
    setSearch,
    isLoading,
    hasMore,
    loadMore,
    refresh,
    rename,
    remove,
  }
}
