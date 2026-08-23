/**
 * Tests for the account-creation screen.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { User } from '../lib/api'
import { AuthProvider } from '../contexts/AuthProvider'
import SignupPage from './SignupPage'

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

function renderSignupPage() {
  return render(
    <MemoryRouter initialEntries={['/signup']}>
      <AuthProvider>
        <SignupPage />
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
  it('renders the signup form', () => {
    renderSignupPage()

    expect(
      screen.getByRole('heading', { name: 'Create your account' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create account' })).toBeInTheDocument()
  })

  it('offers a route back to the login page', () => {
    renderSignupPage()

    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      '/login',
    )
  })
})

describe('password length feedback', () => {
  it('keeps submission disabled until the password is long enough', async () => {
    renderSignupPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'short')

    expect(screen.getByRole('button', { name: 'Create account' })).toBeDisabled()
  })

  it('enables submission once the minimum length is met', async () => {
    renderSignupPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-enough-password')

    expect(screen.getByRole('button', { name: 'Create account' })).toBeEnabled()
  })
})

describe('submitting', () => {
  it('sends the typed details to the API', async () => {
    vi.mocked(api.signup).mockResolvedValue(ADA)
    renderSignupPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(api.signup).toHaveBeenCalledWith('ada@example.com', 'a-long-enough-password')
  })

  it('surfaces a duplicate-email rejection from the server', async () => {
    const { ApiError } = await import('../lib/api')
    vi.mocked(api.signup).mockRejectedValue(
      new ApiError('An account with that email already exists'),
    )
    renderSignupPage()

    await userEvent.type(screen.getByLabelText('Email'), 'ada@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'An account with that email already exists',
    )
  })
})
