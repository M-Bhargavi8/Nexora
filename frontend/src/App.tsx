/**
 * The route table.
 *
 * Every route is wrapped in the guard that expresses its access rule, rather
 * than each page checking for itself. Keeping the rule beside the route means
 * you can read this one file and know exactly which pages are protected --
 * instead of trusting that nobody forgot a check inside a component.
 *
 *   /login, /signup   public, but bounce an already signed-in user home
 *   /                 protected: requires a valid session
 *   anything else     redirect home, which then applies the rule above
 */

import { Navigate, Route, Routes } from 'react-router'
import { RedirectIfAuthenticated } from './components/RedirectIfAuthenticated'
import { RequireAuth } from './components/RequireAuth'
import LoginPage from './pages/LoginPage'
import SignupPage from './pages/SignupPage'
import StatusPage from './pages/StatusPage'

export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RedirectIfAuthenticated>
            <LoginPage />
          </RedirectIfAuthenticated>
        }
      />
      <Route
        path="/signup"
        element={
          <RedirectIfAuthenticated>
            <SignupPage />
          </RedirectIfAuthenticated>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <StatusPage />
          </RequireAuth>
        }
      />
      {/* Unknown URL: send them home rather than rendering a blank page. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
