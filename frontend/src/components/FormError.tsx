/** A form-level error banner, announced to assistive technology. */

interface FormErrorProps {
  message: string | null
}

export function FormError({ message }: FormErrorProps) {
  if (!message) return null

  return (
    <div
      // role="alert" makes a screen reader announce this the moment it
      // appears -- otherwise a failed login is silent for those users, who
      // are left waiting for something that already happened.
      role="alert"
      className="rounded-lg border border-down-400/30 bg-down-600/10 px-3 py-2.5 text-xs leading-relaxed text-down-400"
    >
      {message}
    </div>
  )
}
