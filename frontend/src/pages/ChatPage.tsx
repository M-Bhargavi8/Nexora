/**
 * The chat workspace: conversation list, transcript, composer.
 *
 * Reaching this page at all means `RequireAuth` resolved a valid session.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Composer } from '../components/Composer'
import { ConversationSidebar } from '../components/ConversationSidebar'
import { MessageList } from '../components/MessageList'
import { useAuth } from '../hooks/useAuth'
import { useChat } from '../hooks/useChat'
import { useConversations } from '../hooks/useConversations'

export default function ChatPage() {
  const { user, logout } = useAuth()
  const chat = useChat()
  const conversations = useConversations()
  const [isSigningOut, setIsSigningOut] = useState(false)

  // The list is ordered by most recent activity and titled from the first
  // message, so it only changes once a turn has completed.
  const { isStreaming } = chat
  const { refresh } = conversations
  useEffect(() => {
    if (!isStreaming) void refresh()
  }, [isStreaming, refresh])

  async function handleSignOut() {
    setIsSigningOut(true)
    await logout()
  }

  return (
    <div className="flex h-full min-h-0">
      <ConversationSidebar
        list={conversations}
        activeId={chat.conversationId}
        onSelect={(id) => void chat.load(id)}
        onNewChat={chat.reset}
        onActiveDeleted={chat.reset}
      />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-ink-700/60 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="bg-gradient-to-r from-brand-300 via-brand-400 to-halo-400 bg-clip-text text-sm font-semibold text-transparent">
              NEXORA
            </span>
            <span className="rounded border border-ink-700 bg-ink-850 px-1.5 py-0.5 text-[10px] font-medium text-mist-500">
              Phase&nbsp;5
            </span>
          </div>

          <div className="flex items-center gap-3">
            <Link
              to="/files"
              className="text-xs text-mist-500 underline-offset-4 hover:text-mist-300 hover:underline"
            >
              Files
            </Link>
            <Link
              to="/status"
              className="text-xs text-mist-500 underline-offset-4 hover:text-mist-300 hover:underline"
            >
              Status
            </Link>
            <span className="hidden max-w-[180px] truncate text-xs text-mist-500 sm:block">
              {user?.email}
            </span>
            <button
              type="button"
              onClick={() => void handleSignOut()}
              disabled={isSigningOut}
              className="rounded-md border border-ink-600 bg-ink-800 px-2.5 py-1 text-xs font-medium text-mist-300 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:opacity-50"
            >
              {isSigningOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </header>

        {/* No edit handler while a reply is streaming: rewinding the thread
            underneath a reply that is still being written would leave the
            server saving it into a conversation that no longer has that turn. */}
        <MessageList
          messages={chat.messages}
          onEdit={
            chat.isStreaming
              ? undefined
              : (messageId, content) => void chat.edit(messageId, content)
          }
        />

        {chat.canRegenerate && (
          <div className="px-4">
            <div className="mx-auto flex max-w-3xl justify-center">
              <button
                type="button"
                onClick={() => void chat.regenerate()}
                className="rounded-lg border border-ink-600 bg-ink-800 px-3 py-1.5 text-xs font-medium text-mist-300 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                Regenerate reply
              </button>
            </div>
          </div>
        )}

        {chat.error && (
          <div className="px-4">
            <p
              role="alert"
              className="mx-auto max-w-3xl rounded-lg border border-down-400/25 bg-down-600/10 px-3 py-2 text-xs text-down-400"
            >
              {chat.error}
            </p>
          </div>
        )}

        <Composer
          onSend={(content) => void chat.send(content)}
          onStop={chat.stop}
          isStreaming={chat.isStreaming}
        />
      </div>
    </div>
  )
}
