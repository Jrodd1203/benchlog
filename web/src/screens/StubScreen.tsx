import { FutureButton } from '../components/FutureButton'
import type { StubSection } from './stubs'

/** Skeleton screen for a requirement that isn't built yet. */
export function StubScreen({ section }: { section: StubSection }) {
  return (
    <main className="page narrow">
      <header className="page-head">
        <div>
          <p className="eyebrow">Requirement {section.requirement}, not built yet</p>
          <h1>{section.title}</h1>
        </div>
      </header>
      <section className="panel">
        <h2>Planned</h2>
        <ul className="feature-list">
          {section.features.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
        <div className="row">
          {section.actions.map((a) => (
            <FutureButton key={a.label} needs={a.needs} primary={a.primary}>
              {a.label}
            </FutureButton>
          ))}
        </div>
      </section>
    </main>
  )
}
