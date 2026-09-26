import { FutureButton } from '../components/FutureButton'

export interface StubSection {
  title: string
  requirement: string
  /** What the screen will show, from the team's requirements list. */
  features: string[]
  /** Actions, each with what it needs before it can work. */
  actions: { label: string; needs: string; primary?: boolean }[]
}

/** Skeleton screens for requirements that aren't built yet. Numbers match the team's Notion list. */
export const STUBS = {
  review: {
    title: 'Proposal review',
    requirement: '4',
    features: [
      'Detected changes highlighted on the virtual breadboard: added green, removed red, moved yellow',
      'Verdict badge on each change: confirmed, conflict, camera only',
      'Accept or reject each change',
      "If the camera isn't sure, pick the right hole on the virtual board",
      'Optional "view photo" for the camera crop of a change',
    ],
    actions: [
      { label: 'Accept all confirmed', needs: 'POST /api/observations/accept', primary: true },
      { label: 'Reject', needs: 'POST /api/observations/reject' },
      { label: 'View photo', needs: 'scan image crops from the vision pipeline' },
    ],
  },
  serial: {
    title: 'Serial (ESP32)',
    requirement: '5',
    features: [
      'Port picker and Connect button',
      'Status light: ESP32 connected / not connected',
      'Warnings like "wire may not be seated" or "sensor not responding"',
    ],
    actions: [
      { label: 'Refresh ports', needs: 'GET /api/serial/ports' },
      { label: 'Connect', needs: 'POST /api/serial/connect', primary: true },
      { label: 'Probe now', needs: 'POST /api/serial/probe' },
    ],
  },
  commits: {
    title: 'Changes and commits',
    requirement: '6',
    features: ['Unstaged and staged changes', 'Stage the circuit, write a message, commit', 'Optional "mark as tested" on a commit'],
    actions: [
      { label: 'Stage circuit', needs: 'a staging endpoint' },
      { label: 'Commit', needs: 'POST /api/commit', primary: true },
      { label: 'Mark as tested', needs: 'a "tested" flag on commits (backend)' },
    ],
  },
  diff: {
    title: 'Diff',
    requirement: '8',
    features: [
      'Compare any two versions: parts and wires added, removed, or moved',
      'Shows whether a change is electrical or just a different hole',
    ],
    actions: [{ label: 'Compare versions', needs: 'GET /api/diff?old=&new= + two-version picker', primary: true }],
  },
  checks: {
    title: 'Checks',
    requirement: '9',
    features: [
      'Power-to-ground short',
      'Unconfirmed wires',
      'Camera vs. serial conflicts',
      'I2C sensor not responding',
      'Results: pass / fail / needs confirmation',
    ],
    actions: [{ label: 'Run checks', needs: '`benchlog check` exposed as an API endpoint (Person 2)', primary: true }],
  },
  github: {
    title: 'GitHub: branches and PRs',
    requirement: '10',
    features: [
      'Create a branch to try a change without breaking main',
      'Open a PR with a before/after virtual breadboard for the two branches',
      'Checks run automatically on the PR (GitHub Actions)',
      'Merge button, enabled only when checks pass',
    ],
    actions: [
      { label: 'Connect GitHub', needs: 'GitHub auth (gh CLI or OAuth) on the backend' },
      { label: 'New branch', needs: 'a branch endpoint (core/repo.py)' },
      { label: 'Push', needs: 'a push endpoint + GitHub auth' },
      { label: 'Open PR', needs: 'GitHub API: create pull request', primary: true },
      { label: 'Merge', needs: 'GitHub API: merge, only when PR checks pass' },
    ],
  },
  issues: {
    title: 'Issues',
    requirement: '11',
    features: ['Report a problem tied to a specific part and commit', 'Expected vs. observed behavior'],
    actions: [{ label: 'New issue', needs: 'an issues store (local file or GitHub Issues API)', primary: true }],
  },
} satisfies Record<string, StubSection>

export type StubId = keyof typeof STUBS

export function StubScreen({ section }: { section: StubSection }) {
  return (
    <main className="page narrow">
      <header className="page-head">
        <div>
          <p className="eyebrow">Requirement {section.requirement} · skeleton</p>
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
