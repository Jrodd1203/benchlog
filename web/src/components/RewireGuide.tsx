import type { RewireStep } from '../api/prs'

/** Ordered rewiring steps. Same-strip moves are marked optional (no electrical change). */
export function RewireGuide({ steps }: { steps: RewireStep[] }) {
  return (
    <ol className="guide">
      {steps.map((s) => (
        <li key={s.step} className={s.optional ? 'optional' : undefined}>
          {s.text}
        </li>
      ))}
    </ol>
  )
}
