/**
 * Tests for the route guards.
 *
 * These cover the bug that guards get wrong most often: treating "we do not
 * know yet" as "signed out", which bounces a valid session to the login page
 * on every refresh.
 */

import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { User } from '../lib/api'
import { AuthProvider } from '../contexts/AuthProvider'
import { RedirectIfAuthenticated } from './RedirectIfAuthenticated'
import { RequireAuth } from './RequireAuth'

vi.mock('../lib/api', () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  signup: vi.fn(),
  logout: vi.fn(),
  API_BASE_URL: 'http://localhost:8000',
  ApiError: class ApiError extends Error {},
}))

const ADA: User = {
  id: '11111111-1111-1111-1111-111111111111',
  email: 'ada@example.com',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

/** The three routes under test, mounted at `startAt`. */
function renderRoutes(startAt: string) {
  return render(
    <MemoryRouter initialEntries={[startAt]}>
      <AuthProvider>
        <Routes>
          <Route
            path="/login"
            element={
              <RedirectIfAuthenticated>
                <p>login page</p>
              </RedirectIfAuthenticated>
            }
          />
          <Route
            path="/"
            element={
              <RequireAuth>
                <p>protected workspace</p>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.getCurrentUser).mockResolvedValue(null)
})

describe('RequireAuth', () => {
  it('redirects an unauthenticated visitor to the login page', async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(null)

    renderRoutes('/')

    expect(await screen.findByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('protected workspace')).not.toBeInTheDocument()
  })

  it('renders the protected page for a signed-in user', async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)

    renderRoutes('/')

    expect(await screen.findByText('protected workspace')).toBeInTheDocument()
  })

  it('waits instead of redirecting while the session is still unknown', () => {
    // The bug this prevents: redirecting during initialization sends a
    // perfectly valid session to /login on every page refresh, because the
    // "am I signed in?" request has not come back yet.
    vi.mocked(api.getCurrentUser).mockReturnValue(new Promise<User | null>(() => {}))

    renderRoutes('/')

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByText('login page')).not.toBeInTheDocument()
    expect(screen.queryByText('protected workspace')).not.toBeInTheDocument()
  })

  it('does not hide the protected page from a user whose API call failed', async () => {
    // An unreachable API means "not signed in", so the guard must send them
    // to login rather than leaving a blank screen.
    vi.mocked(api.getCurrentUser).mockRejectedValue(new Error('network down'))

    renderRoutes('/')

    expect(await screen.findByText('login page')).toBeInTheDocument()
  })
})

describe('RedirectIfAuthenticated', () => {
  it('lets a signed-out visitor see the login page', async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(null)

    renderRoutes('/login')

    expect(await screen.findByText('login page')).toBeInTheDocument()
  })

  it('sends an already signed-in user to the workspace', async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)

    renderRoutes('/login')

    expect(await screen.findByText('protected workspace')).toBeInTheDocument()
    expect(screen.queryByText('login page')).not.toBeInTheDocument()
  })
})
