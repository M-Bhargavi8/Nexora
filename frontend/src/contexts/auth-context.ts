/**
 * The authentication context object and the shape of its value.
 *
 * Deliberately a plain `.ts` module that exports no components. Vite's fast
 * refresh can only swap a module in place when that module exports components
 * exclusively; mixing a component with a context object downgrades every edit
 * to a full page reload -- which silently throws away the signed-in state you
 * were in the middle of testing. `useAuth` lives in its own file for exactly
 * the same reason.
 */

import { createContext } from 'react'
import type { User } from '../lib/api'

export interface AuthContextValue {
  user: User | null
  /** True until the initial "am I signed in?" check completes. */
  isInitializing: boolean
  signup: (email: string, password: string) => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)
