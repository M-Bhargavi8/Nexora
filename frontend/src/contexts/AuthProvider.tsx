/**
 * Authentication state, shared across the whole application.
 *
 * React Context solves "many components at different depths need the same
 * value". Without it, the current user would have to be passed down through
 * every intermediate component that does not care about it -- "prop
 * drilling". Auth is the textbook case for context: it is needed everywhere
 * and changes rarely.
 *
 * Note what is NOT stored here: the session token. It lives in an httpOnly
 * cookie that JavaScript cannot read, which is precisely the point -- an XSS
 * bug cannot steal what it cannot access. Nor is it in localStorage or
 * sessionStorage, both of which are readable by any script on the page. This
 * context holds only the user's public profile.
 *
 * The context object itself lives in `auth-context.ts` so that this module
 * exports only a component, which is what Vite's fast refresh needs.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import * as api from '../lib/api'
import type { User } from '../lib/api'
import { AuthContext, type AuthContextValue } from './auth-context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isInitializing, setIsInitializing] = useState(true)

  // On first load, ask the server who we are. The browser may already hold a
  // valid session cookie from a previous visit, and since it is httpOnly we
  // cannot inspect it ourselves -- only the server can answer.
  useEffect(() => {
    let cancelled = false

    async function restoreSession() {
      try {
        const currentUser = await api.getCurrentUser()
        if (!cancelled) setUser(currentUser)
      } catch {
        // The API being unreachable is not an auth failure. Treat it as
        // "not signed in" and let the page surface the connection problem.
        if (!cancelled) setUser(null)
      } finally {
        if (!cancelled) setIsInitializing(false)
      }
    }

    void restoreSession()
    return () => {
      cancelled = true
    }
  }, [])

  const signup = useCallback(async (email: string, password: string) => {
    setUser(await api.signup(email, password))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    setUser(await api.login(email, password))
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      // Deliberately swallowed. `finally` alone does NOT stop a rejection
      // propagating, so this used to reject the promise returned by logout()
      // -- and every caller fires it from an onClick and discards that
      // promise, turning a failed sign-out into an unhandled rejection.
    } finally {
      // Clear local state even if the request failed. Leaving someone who
      // clicked "sign out" looking signed in is the worse outcome, and the
      // server-side session is already gone in the common case.
      setUser(null)
    }
  }, [])

  // useMemo keeps the context value referentially stable. Without it, a new
  // object would be created on every render and force every consuming
  // component to re-render, whether or not anything actually changed.
  const value = useMemo<AuthContextValue>(
    () => ({ user, isInitializing, signup, login, logout }),
    [user, isInitializing, signup, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
