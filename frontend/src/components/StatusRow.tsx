/** One row of the system status panel: a component and its current state. */

import { StatusPill, type PillTone } from './StatusPill'

interface StatusRowProps {
  name: string
  description: string
  tone: PillTone
  pillLabel: string
  detail?: string | null
}

export function StatusRow({
  name,
  description,
  tone,
  pillLabel,
  detail,
}: StatusRowProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-700/60 px-5 py-4 first:border-t-0">
      <div className="min-w-0">
        <p className="text-sm font-medium text-mist-100">{name}</p>
        <p className="mt-0.5 text-xs text-mist-500">{detail ?? description}</p>
      </div>
      <StatusPill tone={tone} label={pillLabel} />
    </div>
  )
}
