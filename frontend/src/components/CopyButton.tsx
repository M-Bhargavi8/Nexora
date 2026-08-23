/** Copies text to the clipboard and confirms it briefly. */

import { useCallback, useEffect, useRef, useState } from 'react'

interface CopyButtonProps {
  text: string
  label?: string
  className?: string
}

export function CopyButton({ text, label = 'Copy', className = '' }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Without this, clicking Copy and then navigating away leaves a timer that
  // fires against an unmounted component.
  useEffect(
    () => () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    },
    [],
  )

  const handleCopy = useCallback(async () => {
    try {
      // Absent in insecure contexts and in the test DOM, so it is checked
      // rather than assumed. A missing clipboard should do nothing, not throw.
      await navigator.clipboard?.writeText(text)
      setCopied(true)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      // Denied permission is not worth an error banner over.
    }
  }, [text])

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      // The label changes to "Copied", which screen readers announce, so no
      // separate live region is needed.
      aria-label={copied ? 'Copied' : label}
      className={`rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-[11px] font-medium text-mist-400 transition-colors hover:border-brand-500/50 hover:text-brand-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 ${className}`}
    >
      {copied ? 'Copied' : label}
    </button>
  )
}
