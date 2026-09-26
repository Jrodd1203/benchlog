import type { ReactNode } from 'react'

/**
 * A button for a feature that isn't wired up yet. It renders normally, does nothing when
 * clicked, and carries a small "soon" tag. `needs` says what's missing, shown on hover.
 * Search the code for <FutureButton to find every unfinished action.
 */
export function FutureButton({
  children,
  needs,
  primary = false,
}: {
  children: ReactNode
  needs: string
  primary?: boolean
}) {
  return (
    <button
      type="button"
      className={`btn future${primary ? ' primary' : ''}`}
      title={`Not wired yet: ${needs}`}
    >
      {children}
      <span className="soon-tag">soon</span>
    </button>
  )
}
