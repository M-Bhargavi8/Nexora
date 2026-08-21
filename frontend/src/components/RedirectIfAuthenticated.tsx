/**
 * The mirror of RequireAuth: keeps an already signed-in user off /login and
 * /signup.
 *
 * Without it, someone who is authenticated can navigate back to the login
 * form and sign in again, creating a second session row for no reason -- and
 * landing on a form that makes them look signed out when they are not.
 */

import type { ReactNode } from 'react'
import { Navigate } from 'react-router'
import { useAuth } from '../hooks/useAuth'
import { LoadingScreen } from './LoadingScreen'

export function RedirectIfAuthenticated({ children }: { children: ReactNode }) {
  const { user, isInitializing } = useAuth()

  if (isInitializing) {
    return <LoadingScreen label="Checking your session…" />
  }

  if (user) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
