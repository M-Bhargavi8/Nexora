/**
 * NEXORA — system status page.
 *
 * This page exists to prove the full chain works end to end:
 *   browser -> Vite dev server -> FastAPI -> PostgreSQL
 *
 * From Phase 2 it also serves as the protected application route: reaching it
 * at all means `RequireAuth` resolved a valid session. It is replaced by the
 * real workspace UI in Phase 3.
 */

import { useState } from 'react'
import { StatusRow } from '../components/StatusRow'
import { type PillTone } from '../components/StatusPill'
import { useSystemStatus } from '../hooks/useSystemStatus'
import { useAuth } from '../hooks/useAuth'
import { API_BASE_URL } from '../lib/api'

/** Map a component's loading/up state onto a pill colour. */
function statusTone(isLoading: boolean, isUp: boolean): PillTone {
  if (isLoading) return 'pending'
  return isUp ? 'ok' : 'down'
}

export default function StatusPage() {
  const { isLoading, health, readiness, error, lastCheckedAt, latencyMs, refresh } =
    useSystemStatus()
  const { user, logout } = useAuth()
  const [isSigningOut, setIsSigningOut] = useState(false)

  const apiIsUp = health !== null
  const databaseCheck = readiness?.checks.find((check) => check.name === 'database')
  const databaseIsUp = databaseCheck?.healthy === true

  async function handleSignOut() {
    setIsSigningOut(true)
    // No navigation needed: clearing the user makes RequireAuth redirect to
    // /login on the next render. That keeps one source of truth for "where do
    // I go when signed out" instead of repeating it at every call site.
    await logout()
  }

  return (
    <div className="relative min-h-full overflow-hidden">
      {/* Decorative background glow. aria-hidden so screen readers skip it. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(60%_100%_at_50%_0%,rgba(28,196,176,0.16),transparent_70%)]"
      />

      <div className="relative mx-auto flex min-h-full max-w-3xl flex-col px-6 py-14 sm:py-20">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="bg-gradient-to-r from-brand-300 via-brand-400 to-halo-400 bg-clip-text text-3xl font-semibold tracking-tight text-transparent">
                NEXORA
              </h1>
              <span className="rounded-md border border-ink-700 bg-ink-850 px-2 py-0.5 text-[11px] font-medium text-mist-500">
                Phase&nbsp;2
              </span>
            </div>
            <p className="mt-2 text-sm text-mist-300">Your Intelligent AI Workspace</p>
          </div>

          <div className="flex items-center gap-3">
            <span
              className="max-w-[200px] truncate text-xs text-mist-500"
              title={user?.email}
            >
              {user?.email}
            </span>
            <button
              type="button"
              onClick={() => void handleSignOut()}
              disabled={isSigningOut}
              className="rounded-md border border-ink-600 bg-ink-800 px-3 py-1.5 text-xs font-medium text-mist-300 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSigningOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </header>

        <main className="mt-10">
          <section
            aria-labelledby="system-status-heading"
            className="overflow-hidden rounded-xl border border-ink-700/70 bg-ink-900/70 shadow-2xl shadow-black/40 backdrop-blur"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/60 bg-ink-850/60 px-5 py-3.5">
              <h2
                id="system-status-heading"
                className="text-sm font-semibold text-mist-100"
              >
                System status
              </h2>
              <button
                type="button"
                onClick={refresh}
                disabled={isLoading}
                className="rounded-md border border-ink-600 bg-ink-800 px-3 py-1.5 text-xs font-medium text-mist-300 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isLoading ? 'Checking…' : 'Refresh'}
              </button>
            </div>

            {/* aria-live announces status changes to screen-reader users
                without them needing to re-read the page. */}
            <div aria-live="polite">
              <StatusRow
                name="API"
                description="FastAPI backend"
                tone={statusTone(isLoading, apiIsUp)}
                pillLabel={isLoading ? 'Checking' : apiIsUp ? 'Online' : 'Offline'}
                detail={
                  apiIsUp
                    ? `FastAPI · v${health.version} · ${health.environment}`
                    : error
                }
              />
              <StatusRow
                name="Database"
                description="PostgreSQL + pgvector"
                tone={statusTone(isLoading, databaseIsUp)}
                pillLabel={
                  isLoading ? 'Checking' : databaseIsUp ? 'Connected' : 'Unavailable'
                }
                detail={
                  databaseIsUp
                    ? 'PostgreSQL · connection pool healthy'
                    : (databaseCheck?.detail ?? 'PostgreSQL + pgvector')
                }
              />
            </div>

            <dl className="grid grid-cols-2 gap-px border-t border-ink-700/60 bg-ink-700/40 text-xs sm:grid-cols-3">
              <div className="bg-ink-900 px-5 py-3">
                <dt className="text-mist-500">Endpoint</dt>
                <dd
                  className="mt-1 truncate font-mono text-mist-300"
                  title={API_BASE_URL}
                >
                  {API_BASE_URL}
                </dd>
              </div>
              <div className="bg-ink-900 px-5 py-3">
                <dt className="text-mist-500">Round trip</dt>
                <dd className="mt-1 font-mono text-mist-300">
                  {latencyMs === null ? '—' : `${latencyMs} ms`}
                </dd>
              </div>
              <div className="col-span-2 bg-ink-900 px-5 py-3 sm:col-span-1">
                <dt className="text-mist-500">Last checked</dt>
                <dd className="mt-1 font-mono text-mist-300">
                  {lastCheckedAt?.toLocaleTimeString() ?? '—'}
                </dd>
              </div>
            </dl>
          </section>

          {!isLoading && !databaseIsUp && apiIsUp && (
            <p className="mt-4 rounded-lg border border-warn-400/25 bg-warn-400/5 px-4 py-3 text-xs leading-relaxed text-mist-300">
              <span className="font-medium text-warn-400">Database offline.</span>{' '}
              The API is healthy and correctly reporting that PostgreSQL is not
              reachable. Start it with{' '}
              <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-mist-100">
                docker compose up -d
              </code>{' '}
              from the project root.
            </p>
          )}
        </main>

        <footer className="mt-auto pt-12 text-xs text-mist-500">
          Phase 2 — Authentication. The workspace interface arrives in Phase 3.
        </footer>
      </div>
    </div>
  )
}
