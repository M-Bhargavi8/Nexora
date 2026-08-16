/** A small coloured badge showing the state of one system component. */

export type PillTone = 'ok' | 'down' | 'pending'

const TONE_STYLES: Record<PillTone, string> = {
  ok: 'bg-ok-600/15 text-ok-400 ring-ok-400/30',
  down: 'bg-down-600/15 text-down-400 ring-down-400/30',
  pending: 'bg-ink-600/40 text-mist-500 ring-mist-500/20',
}

const DOT_STYLES: Record<PillTone, string> = {
  ok: 'bg-ok-400',
  down: 'bg-down-400',
  pending: 'bg-mist-500 animate-pulse',
}

interface StatusPillProps {
  tone: PillTone
  label: string
}

export function StatusPill({ tone, label }: StatusPillProps) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${TONE_STYLES[tone]}`}
    >
      {/* aria-hidden: the dot is decorative, the text already conveys state */}
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${DOT_STYLES[tone]}`}
      />
      {label}
    </span>
  )
}
