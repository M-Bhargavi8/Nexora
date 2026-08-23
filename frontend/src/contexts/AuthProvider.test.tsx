/**
 * Tests for the authentication state container.
 *
 * The API module is mocked entirely: these tests must never touch a network,
 * a database, or any external service. What is under test is how the app
 * *reacts* to each possible answer, not whether the server gives it.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { User } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { AuthProvider } from './AuthProvider'

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

function Harness() {
  const { user, isInitializing, logout } = useAuth()

  return (
    <div>
      {isInitializing ? (
        <p>checking session</p>
      ) : (
        <p>{user ? `signed in as ${user.email}` : 'signed out'}</p>
      )}
      <button type="button" onClick={() => void logout()}>
        Sign out
      </button>
    </div>
  )
}

function renderHarness() {
  return render(
    <AuthProvider>
      <Harness />
    </AuthProvider>,
  )
}

beforeEach(() => {
  // Explicit: `restoreMocks` only restores vi.spyOn spies, so the vi.fn()s
  // created inside a vi.mock factory keep their call history between tests.
  // Without this, a call-count assertion silently measures earlier tests too.
  vi.clearAllMocks()
  vi.mocked(api.getCurrentUser).mockResolvedValue(null)
  vi.mocked(api.logout).mockResolvedValue(undefined)
})

describe('initial session restoration', () => {
  it('shows a loading state while the session check is in flight', () => {
    // A promise that never settles, so the component stays in its
    // "still asking" state for the duration of the assertion.
    vi.mocked(api.getCurrentUser).mockReturnValue(new Promise<User | null>(() => {}))

    renderHarness()

    expect(screen.getByText('checking session')).toBeInTheDocument()
  })

  it('reports a signed-out user when the server says there is no session', async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(null)

    renderHarness()

    expect(await screen.findByText('signed out')).toBeInTheDocument()
  })

  it('restores the user when a valid cookie is already held', async () => {
    // The session cookie is httpOnly, so the app cannot read it. Asking the
    // server on load is the only way to know -- this is that behaviour.
    vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)

    renderHarness()

    expect(await screen.findByText('signed in as ada@example.com')).toBeInTheDocument()
  })

  it('treats an unreachable API as signed out rather than crashing', async () => {
    vi.mocked(api.getCurrentUser).mockRejectedValue(new Error('network down'))

    renderHarness()

    expect(await screen.findByText('signed out')).toBeInTheDocument()
  })
})

describe('logout', () => {
  it('clears the user and tells the server to revoke the session', async () => {
    vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)
    renderHarness()
    await screen.findByText('signed in as ada@example.com')

    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByText('signed out')).toBeInTheDocument()
    expect(api.logout).toHaveBeenCalledOnce()
  })

  it('still signs out locally when the logout request fails', async () => {
    // Leaving someone who clicked "sign out" looking signed in is the worse
    // outcome, so local state is cleared regardless.
    vi.mocked(api.getCurrentUser).mockResolvedValue(ADA)
    vi.mocked(api.logout).mockRejectedValue(new Error('network down'))
    renderHarness()
    await screen.findByText('signed in as ada@example.com')

    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByText('signed out')).toBeInTheDocument()
  })
})

describe('useAuth outside a provider', () => {
  it('fails with a message that says what is wrong', () => {
    // Without this guard the symptom is "cannot read property 'user' of
    // undefined", which says nothing about the actual mistake.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => render(<Harness />)).toThrow(/must be used inside an <AuthProvider>/)

    consoleError.mockRestore()
  })
})
