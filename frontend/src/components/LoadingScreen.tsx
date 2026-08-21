/** Full-height placeholder shown while an answer we need is still in flight. */

interface LoadingScreenProps {
  label?: string
}

export function LoadingScreen({ label = 'Loading…' }: LoadingScreenProps) {
  return (
    // role="status" + aria-live means a screen reader announces the wait
    // instead of landing on an apparently empty page.
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-full items-center justify-center px-6 py-20"
    >
      <div className="flex items-center gap-3 text-sm text-mist-300">
        <span
          aria-hidden="true"
          className="h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-brand-400"
        />
        {label}
      </div>
    </div>
  )
}
