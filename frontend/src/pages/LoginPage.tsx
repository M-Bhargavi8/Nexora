import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { AuthCard } from '../components/AuthCard'
import { FormError } from '../components/FormError'
import { Button } from '../components/ui/Button'
import { TextField } from '../components/ui/TextField'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../lib/api'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Where the user was heading before being redirected here, so they land
  // back on it after signing in instead of always being dumped on the home
  // page. ProtectedRoute records this.
  const returnTo = (location.state as { from?: string } | null)?.from ?? '/'

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    // Without this the browser performs a full page navigation and the React
    // app reloads from scratch, discarding all state.
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)

    try {
      await login(email, password)
      // `replace` so the back button does not return to the login form of a
      // session the user has already completed.
      void navigate(returnTo, { replace: true })
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Something went wrong. Please try again.',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthCard
      title="Sign in"
      subtitle="Continue to your workspace"
      footer={
        <>
          New to NEXORA?{' '}
          <Link
            to="/signup"
            className="font-medium text-brand-400 underline-offset-4 hover:underline"
          >
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <FormError message={error} />

        <TextField
          label="Email"
          type="email"
          name="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          // Helps password managers fill and save credentials correctly.
          autoComplete="email"
          required
          autoFocus
          placeholder="you@example.com"
          disabled={isSubmitting}
        />

        <TextField
          label="Password"
          type="password"
          name="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
          placeholder="••••••••••••"
          disabled={isSubmitting}
        />

        <Button type="submit" isLoading={isSubmitting}>
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </AuthCard>
  )
}
