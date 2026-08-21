import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { AuthCard } from '../components/AuthCard'
import { FormError } from '../components/FormError'
import { Button } from '../components/ui/Button'
import { TextField } from '../components/ui/TextField'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../lib/api'

/** Mirrors the backend's MIN_PASSWORD_LENGTH. */
const MIN_PASSWORD_LENGTH = 12

export default function SignupPage() {
  const { signup } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Client-side checks give instant feedback and save a round trip. They are
  // NOT a security control: anyone can call the API directly, so the backend
  // validates everything again. Client validation is for humans; server
  // validation is for safety.
  const passwordTooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)

    try {
      await signup(email, password)
      void navigate('/', { replace: true })
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
      title="Create your account"
      subtitle="Start building with NEXORA"
      footer={
        <>
          Already have an account?{' '}
          <Link
            to="/login"
            className="font-medium text-brand-400 underline-offset-4 hover:underline"
          >
            Sign in
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
          autoComplete="new-password"
          required
          placeholder="••••••••••••"
          disabled={isSubmitting}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. A memorable phrase works well.`}
          error={
            passwordTooShort
              ? `${MIN_PASSWORD_LENGTH - password.length} more character${
                  MIN_PASSWORD_LENGTH - password.length === 1 ? '' : 's'
                } needed`
              : undefined
          }
        />

        <Button type="submit" isLoading={isSubmitting} disabled={passwordTooShort}>
          {isSubmitting ? 'Creating account…' : 'Create account'}
        </Button>
      </form>
    </AuthCard>
  )
}
