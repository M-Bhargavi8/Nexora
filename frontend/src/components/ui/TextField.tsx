/** A labelled text input with inline error support. */

import { useId, type InputHTMLAttributes } from 'react'

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string
  error?: string
  hint?: string
}

export function TextField({ label, error, hint, ...inputProps }: TextFieldProps) {
  // useId generates an id that is stable across renders and unique per
  // instance, so the same field can appear twice on a page without the
  // <label> pointing at the wrong input.
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-mist-300">
        {label}
      </label>

      <input
        {...inputProps}
        id={id}
        aria-invalid={error ? true : undefined}
        // Points assistive technology at the hint or error text, so a screen
        // reader announces why a field was rejected instead of just "invalid".
        aria-describedby={error ? errorId : hint ? hintId : undefined}
        className={`mt-1.5 w-full rounded-lg border bg-ink-850 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-500 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-0 disabled:cursor-not-allowed disabled:opacity-60 ${
          error
            ? 'border-down-400/60 focus:border-down-400 focus:ring-down-400/30'
            : 'border-ink-600 focus:border-brand-500 focus:ring-brand-500/30'
        }`}
      />

      {error ? (
        <p id={errorId} role="alert" className="mt-1.5 text-xs text-down-400">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="mt-1.5 text-xs text-mist-500">
          {hint}
        </p>
      ) : null}
    </div>
  )
}
