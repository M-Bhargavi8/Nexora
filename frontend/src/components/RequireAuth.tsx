/**
 * Route guard: only a signed-in user gets through.
 *
 * There are three states here, and collapsing any two of them is the classic
 * bug in this component:
 *
 *   initializing -> we do not *yet* know who the user is, because the `/me`
 *                   request has not come back. Redirecting in this state
 *                   bounces a perfectly valid session to /login on every page
 *                   refresh.
 *   signed out    -> redirect to /login, remembering where they were headed.
 *   signed in     -> render the protected page.
 *
 * SECURITY: this is a *convenience*, not a protection. It hides UI; it does
 * not defend data. Anyone can edit client-side state in a debugger, so the
 * real check is `get_current_user` on the server, which every protected
 * endpoint runs. Never let a client-side guard be the only thing standing
 * between a user and someone else's data.
 */

import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from '../hooks/useAuth'
import { LoadingScreen } from './LoadingScreen'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isInitializing } = useAuth()
  const location = useLocation()

  if (isInitializing) {
    return <LoadingScreen label="Restoring your session…" />
  }

  if (!user) {
    return (
      // `state.from` is what LoginPage reads to send them back where they were
      // going. `replace` keeps the guarded URL out of history, so the back
      // button does not bounce between the page and the login form.
      <Navigate to="/login" replace state={{ from: location.pathname }} />
    )
  }

  return <>{children}</>
}
