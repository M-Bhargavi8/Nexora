/**
 * Read the authentication context.
 *
 * Kept in its own file, separate from AuthContext.tsx, because Vite's fast
 * refresh only works reliably when a module exports components *or* plain
 * functions -- not a mix of both.
 */

import { useContext } from 'react'
import { AuthContext, type AuthContextValue } from '../contexts/auth-context'

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)

  if (context === undefined) {
    // Turns a confusing "cannot read property 'user' of undefined" into a
    // message that says exactly what is wrong and how to fix it.
    throw new Error('useAuth must be used inside an <AuthProvider>')
  }

  return context
}
