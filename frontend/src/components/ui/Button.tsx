/** The primary action button, with a built-in pending state. */

import type { ButtonHTMLAttributes, ReactNode } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  isLoading?: boolean
  variant?: 'primary' | 'ghost'
  children: ReactNode
}

const VARIANTS = {
  primary:
    'bg-brand-500 text-ink-950 hover:bg-brand-400 focus-visible:outline-brand-400',
  ghost:
    'border border-ink-600 bg-ink-800 text-mist-300 hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline-brand-500',
} as const

export function Button({
  isLoading = false,
  variant = 'primary',
  children,
  disabled,
  ...buttonProps
}: ButtonProps) {
  return (
    <button
      {...buttonProps}
      // Disabling while a request is in flight prevents a double submit,
      // which would otherwise create two accounts or two sessions.
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      className={`inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]}`}
    >
      {isLoading && (
        <span
          aria-hidden="true"
          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
}
