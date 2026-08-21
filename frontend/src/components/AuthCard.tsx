/** Shared shell for the sign-in and sign-up screens. */

import type { ReactNode } from 'react'

interface AuthCardProps {
  title: string
  subtitle: string
  children: ReactNode
  footer: ReactNode
}

export function AuthCard({ title, subtitle, children, footer }: AuthCardProps) {
  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden px-6 py-12">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(60%_100%_at_50%_0%,rgba(28,196,176,0.16),transparent_70%)]"
      />

      <div className="relative w-full max-w-sm">
        <div className="text-center">
          <span className="bg-gradient-to-r from-brand-300 via-brand-400 to-halo-400 bg-clip-text text-2xl font-semibold tracking-tight text-transparent">
            NEXORA
          </span>
          <h1 className="mt-6 text-xl font-semibold text-mist-100">{title}</h1>
          <p className="mt-1.5 text-sm text-mist-500">{subtitle}</p>
        </div>

        <div className="mt-8 rounded-xl border border-ink-700/70 bg-ink-900/70 p-6 shadow-2xl shadow-black/40 backdrop-blur">
          {children}
        </div>

        <p className="mt-6 text-center text-sm text-mist-500">{footer}</p>
      </div>
    </div>
  )
}
