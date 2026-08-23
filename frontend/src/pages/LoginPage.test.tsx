/**
 * Tests for the sign-in screen.
 *
 * Queries go through roles and labels rather than CSS classes, so they assert
 * what a user (or a screen reader) can actually perceive -- and keep passing
 * when the styling changes.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { User } from '../lib/api'
import { AuthProvider } from '../contexts/AuthProvider'
import LoginPage from './LoginPage'

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

function renderLoginPage() {
  // MemoryRouter, because the page calls useNavigate and useLocation, which
  // throw outside a router. This is exactly what was missing when the page
  // existed but nothing mounted it.
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  // Explicit: `restoreMocks` only restores vi.spyOn spies, so the vi.fn()s
  // created inside a vi.mock factory keep their call history between tests.
  // Without this, a call-count assertion silently measures earlier tests too.
  vi.clearAllMocks()
  vi.mocked(api.getCurrentUser).mockResolvedValue(null)
})

describe('rendering', () => {
  it('renders the sign-in form', () => {
    renderLoginPage()

    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('offers a route to the signup page', () => {
    renderLoginPage()

    expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute(
      'href',
      '/signup',
    )
  })

  it('marks the password field so it is not readable on screen', () => {
    renderLoginPage()

    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })
})

describe('submitting', () => {
  it('sends the typed credentials to the API', async () => {
    vi.mocked(api.login).mockResolvedValue(ADA)
    renderLoginPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(api.login).toHaveBeenCalledWith('ada@example.com', 'a-long-enough-password')
  })
})

describe('error handling', () => {
  it('shows the server message when the credentials are rejected', async () => {
    const { ApiError } = await import('../lib/api')
    vi.mocked(api.login).mockRejectedValue(
      new ApiError('Incorrect email or password'),
    )
    renderLoginPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password-here')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Incorrect email or password')
  })

  it('shows a generic message for a failure that is not from the API', async () => {
    // A raw Error could carry an internal detail, so it is deliberately not
    // shown to the user.
    vi.mocked(api.login).mockRejectedValue(new Error('connect ECONNREFUSED'))
    renderLoginPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong. Please try again.')
    expect(alert).not.toHaveTextContent('ECONNREFUSED')
  })

  it('lets the user try again after a failure', async () => {
    const { ApiError } = await import('../lib/api')
    vi.mocked(api.login).mockRejectedValue(new ApiError('Incorrect email or password'))
    renderLoginPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password-here')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await screen.findByRole('alert')

    // The button must not be stuck in its submitting state.
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })
})
