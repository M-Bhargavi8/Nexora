/** The conversation list: search, select, rename, delete, load more. */

import { useEffect, useRef, useState } from 'react'
import type { ConversationDto } from '../lib/chat'
import type { UseConversations } from '../hooks/useConversations'

interface ConversationSidebarProps {
  list: UseConversations
  activeId: string | null
  onSelect: (id: string) => void
  onNewChat: () => void
  /** Called when the conversation currently on screen has been deleted. */
  onActiveDeleted: () => void
}

interface RowProps {
  conversation: ConversationDto
  isActive: boolean
  onSelect: () => void
  onRename: (title: string) => Promise<void>
  onDelete: () => Promise<void>
}

function ConversationRow({
  conversation,
  isActive,
  onSelect,
  onRename,
  onDelete,
}: RowProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(conversation.title ?? '')
  // Deleting is irreversible, so it takes two clicks rather than one. A modal
  // would be heavier; a second click on a button that has changed its label is
  // enough to make the action deliberate.
  const [isConfirmingDelete, setIsConfirmingDelete] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isEditing) inputRef.current?.select()
  }, [isEditing])

  function startEditing() {
    setDraft(conversation.title ?? '')
    setIsEditing(true)
  }

  async function commit() {
    const trimmed = draft.trim()
    setIsEditing(false)
    // An unchanged or blank title is not a rename. Sending it would spend a
    // request to store what is already there, and the server would reject the
    // blank one anyway.
    if (!trimmed || trimmed === conversation.title) return
    await onRename(trimmed)
  }

  if (isEditing) {
    return (
      <li>
        <input
          ref={inputRef}
          value={draft}
          aria-label="Conversation title"
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void commit()
            // Escape abandons the edit. Without it the only way out of a
            // rename you did not mean to start is to save something.
            if (event.key === 'Escape') setIsEditing(false)
          }}
          className="w-full rounded-md border border-brand-500/50 bg-ink-800 px-2.5 py-2 text-[13px] text-mist-100 focus:outline focus:outline-2 focus:outline-offset-1 focus:outline-brand-500"
        />
      </li>
    )
  }

  return (
    <li className="group/row relative">
      <button
        type="button"
        onClick={onSelect}
        onDoubleClick={startEditing}
        aria-current={isActive ? 'true' : undefined}
        className={`w-full truncate rounded-md py-2 pr-16 pl-2.5 text-left text-[13px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 ${
          isActive
            ? 'bg-ink-800 text-mist-100'
            : 'text-mist-400 hover:bg-ink-850 hover:text-mist-200'
        }`}
      >
        {conversation.title ?? 'New conversation'}
      </button>

      {/* Revealed on hover or keyboard focus, so the controls stay reachable
          without a mouse instead of being hover-only. */}
      <div className="absolute top-1/2 right-1 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
        {isConfirmingDelete ? (
          <>
            <button
              type="button"
              onClick={() => void onDelete()}
              className="rounded px-1.5 py-1 text-[11px] font-medium text-down-400 hover:bg-down-600/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => setIsConfirmingDelete(false)}
              aria-label="Cancel deleting"
              className="rounded px-1.5 py-1 text-[11px] text-mist-500 hover:text-mist-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={startEditing}
              aria-label={`Rename ${conversation.title ?? 'conversation'}`}
              className="rounded px-1.5 py-1 text-[11px] text-mist-500 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              Rename
            </button>
            <button
              type="button"
              onClick={() => setIsConfirmingDelete(true)}
              aria-label={`Delete ${conversation.title ?? 'conversation'}`}
              className="rounded px-1.5 py-1 text-[11px] text-mist-500 hover:text-down-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              Delete
            </button>
          </>
        )}
      </div>
    </li>
  )
}

export function ConversationSidebar({
  list,
  activeId,
  onSelect,
  onNewChat,
  onActiveDeleted,
}: ConversationSidebarProps) {
  const { conversations, search, setSearch, isLoading, hasMore } = list

  async function handleDelete(id: string) {
    await list.remove(id)
    // The open conversation just stopped existing. Leaving its messages on
    // screen would offer a Send button that answers 404.
    if (id === activeId) onActiveDeleted()
  }

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-ink-700/60 bg-ink-950/60 md:flex">
      <div className="space-y-2 p-3">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full rounded-lg border border-ink-600 bg-ink-800 px-3 py-2 text-sm font-medium text-mist-200 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
        >
          New chat
        </button>

        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search conversations"
          aria-label="Search conversations"
          className="w-full rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-mist-200 placeholder:text-mist-600 focus:border-brand-500/50 focus:outline focus:outline-2 focus:outline-offset-1 focus:outline-brand-500"
        />
      </div>

      <nav
        aria-label="Conversations"
        className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
      >
        {conversations.length === 0 ? (
          <p className="px-2 py-3 text-xs text-mist-500">
            {search
              ? 'No conversations match that search.'
              : 'No conversations yet.'}
          </p>
        ) : (
          <>
            <ul className="space-y-0.5">
              {conversations.map((conversation) => (
                <ConversationRow
                  key={conversation.id}
                  conversation={conversation}
                  isActive={conversation.id === activeId}
                  onSelect={() => onSelect(conversation.id)}
                  onRename={(title) => list.rename(conversation.id, title)}
                  onDelete={() => handleDelete(conversation.id)}
                />
              ))}
            </ul>

            {hasMore && (
              <button
                type="button"
                onClick={() => void list.loadMore()}
                disabled={isLoading}
                className="mt-2 w-full rounded-md px-2.5 py-2 text-xs text-mist-500 transition-colors hover:bg-ink-850 hover:text-mist-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:opacity-50"
              >
                {isLoading ? 'Loading…' : 'Load more'}
              </button>
            )}
          </>
        )}
      </nav>
    </aside>
  )
}
