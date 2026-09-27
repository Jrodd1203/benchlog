import { useEffect, useState } from 'react'
import { createProject, fetchRemoteCircuit, getCircuitAt, getTimeline, listProjects } from '../api'
import { Breadboard } from '../board/Breadboard'
import { NewProjectModal } from '../components/NewProjectModal'
import type { Circuit, ProjectSummary, TimelineEntry } from '../types'

const EXPLORE_PROJECTS = [
  { label: 'Pot + LED', owner: 'Jrodd1203', repo: 'bench-pot-led' },
  { label: 'Weather Station', owner: 'benchlog-team', repo: 'bench-weather-station' },
  { label: 'LED Bar', owner: 'benchlog-team', repo: 'bench-led-bar' },
]

function ExploreSection() {
  const [loaded, setLoaded] = useState<({ label: string; owner: string; repo: string; circuit: Circuit })[] | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all(
      EXPLORE_PROJECTS.map((p) =>
        fetchRemoteCircuit(p.owner, p.repo).then((circuit) =>
          circuit ? { ...p, circuit } : null
        )
      )
    ).then((results) => {
      if (alive) setLoaded(results.filter((r) => r !== null))
    })
    return () => { alive = false }
  }, [])

  if (loaded !== null && loaded.length === 0) return null

  return (
    <section className="explore-section">
      <div className="section-label">
        <h2>Explore</h2>
      </div>
      {loaded === null ? (
        <p className="muted small">Loading community projects…</p>
      ) : (
        <ul className="explore-grid">
          {loaded.map((p) => (
            <li key={`${p.owner}/${p.repo}`} className="explore-card">
              <div className="explore-board">
                <Breadboard circuit={p.circuit} />
              </div>
              <span className="project-name">{p.label}</span>
              <span className="muted small">{p.owner}/{p.repo}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

type WorkflowTab = 'scan' | 'review' | 'commit' | 'check'

const WORKFLOW_TABS: { id: WorkflowTab; label: string }[] = [
  { id: 'scan', label: 'Scan' },
  { id: 'review', label: 'Review' },
  { id: 'commit', label: 'Commit' },
  { id: 'check', label: 'Check' },
]

type TermPart = { t: 'prompt' | 'ok' | 'bad' | 'warn' | 'comment' | 'text'; v: string }
type TermLine = TermPart[]

function t(type: TermPart['t'], v: string): TermPart { return { t: type, v } }

const WORKFLOW_CONTENT: Record<WorkflowTab, TermLine[]> = {
  scan: [
    [t('prompt', '$ benchlog scan')],
    [],
    [t('comment', '⠸ Reading board with camera…')],
    [],
    [t('warn', '  ~ w5: A4 → A12  (moved)')],
    [t('warn', '  ~ w5: now connected to GPIO12 (strapping pin)')],
    [],
    [t('text', '2 changes detected. Run '), t('prompt', '`benchlog review`'), t('text', ' to accept.')],
  ],
  review: [
    [t('prompt', '$ benchlog review')],
    [],
    [t('warn', '  ~ w5: A4 → A12  (moved)')],
    [t('comment', '    w5 is now connected to GPIO12 — a strapping pin.')],
    [t('comment', '    This can prevent the ESP32 from booting.')],
    [],
    [t('comment', '  [a] accept  [r] reject  [s] skip  [q] quit')],
    [],
    [t('text', '> a')],
    [t('ok', '✓ accepted: w5 moved to A12')],
  ],
  commit: [
    [t('prompt', '$ benchlog commit -m "Move pot signal to GPIO12"')],
    [],
    [t('text', '[main 4f2a1b3] Move pot signal to GPIO12')],
    [t('comment', ' 1 file changed, 2 insertions(+), 1 deletion(-)')],
    [],
    [t('ok', '✓ Circuit snapshot saved alongside firmware.')],
  ],
  check: [
    [t('prompt', '$ benchlog check')],
    [],
    [t('bad', '  ✗ w5 is connected to GPIO12 — a strapping pin.')],
    [t('comment', '    ESP32 may not boot. Move the wire to a safe GPIO.')],
    [],
    [t('bad', '1 check failed.')],
  ],
}

function WorkflowSection() {
  const [active, setActive] = useState<WorkflowTab>('scan')
  const lines = WORKFLOW_CONTENT[active]
  return (
    <section className="workflow-section">
      <div className="hero-code-panel">
        <div className="hero-code-tabbar">
          {WORKFLOW_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`hero-code-tab${active === tab.id ? ' active' : ''}`}
              onClick={() => setActive(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="hero-code-body">
          {lines.map((parts, i) => (
            <div key={i} className="term-line">
              <span className="term-lnum">{i + 1}</span>
              <span className="term-text">
                {parts.map((p, j) => (
                  <span key={j} className={`term-${p.t}`}>{p.v}</span>
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

const COMMANDS = [
  { name: 'init',     desc: 'Create a benchlog project in the current git repo' },
  { name: 'scan',     desc: 'Read the board with the camera; detect wire changes' },
  { name: 'review',   desc: 'Interactively accept or reject each detected change' },
  { name: 'status',   desc: 'Show the current circuit vs the last commit' },
  { name: 'diff',     desc: 'Show changes between two commits (or HEAD vs last)' },
  { name: 'commit',   desc: 'Save the current circuit + firmware as a new commit' },
  { name: 'log',      desc: 'List commits with their circuit change summary' },
  { name: 'check',    desc: 'Run electrical safety checks on the current circuit' },
  { name: 'branch',   desc: 'List branches, or create one at HEAD' },
  { name: 'checkout', desc: 'Switch to a branch; shows rewiring steps if the board differs' },
  { name: 'board',    desc: 'Does the physical board match the checked-out circuit?' },
  { name: 'pr',       desc: 'Local pull requests: compare a branch with main, check it, merge it' },
  { name: 'camera',   desc: 'Manage camera selection and calibration' },
  { name: 'serial',   desc: 'Set up the ESP32 serial agent that double-checks what the camera sees' },
  { name: 'serve',    desc: 'Start the local API for the web UI' },
]

function CommandsSection() {
  return (
    <section className="commands-section">
      <div className="section-label" style={{ marginBottom: '0.75rem' }}>
        <h2>Commands</h2>
      </div>
      <div className="commands-panel">
        <div className="commands-panel-header">
          <span className="term-prompt">$ </span>benchlog --help
        </div>
        <div className="commands-body">
          <div className="cmd-line" style={{ marginTop: '0.6rem', marginBottom: '0.4rem' }}>Version control for breadboard prototypes.</div>
          <div className="cmd-line" style={{ marginTop: '0.5rem', opacity: 0.6 }}>Commands:</div>
          {COMMANDS.map((c) => (
            <div key={c.name} className="cmd-line">
              {'  '}<span className="cmd-name">{c.name.padEnd(10)}</span>{c.desc}
            </div>
          ))}
          <div className="cmd-line" style={{ marginTop: '0.5rem', opacity: 0.6 }}>Options:</div>
          <div className="cmd-line">{'  '}<span className="cmd-name">{'--help'.padEnd(10)}</span>Show this message and exit.</div>
          <div className="cmd-line">{'  '}<span className="cmd-name">{'--version'.padEnd(10)}</span>Show the version and exit.</div>
        </div>
      </div>
    </section>
  )
}

/** Requirement 1 (landing page): what benchlog is, then the local projects to open. */
export function ProjectsScreen({ onOpen }: { onOpen: (p: ProjectSummary) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    let alive = true
    listProjects().then((p) => {
      if (alive) setProjects(p)
    })
    return () => {
      alive = false
    }
  }, [])

  async function handleCreate(input: { name: string; board: string }) {
    const project = await createProject(input)
    setProjects((prev) => [...(prev ?? []), project])
    setCreating(false)
  }

  return (
    <main className="page">
      <section className="hero">
        <div>
          <h1>benchlog</h1>
          <p className="hero-sub">Version control for hardware</p>
          <p className="hero-pitch">
            Scan your breadboard, review what changed, and commit it with the firmware, so you always know what
            worked.
          </p>
          <ol className="hero-steps">
            <li>Scan the board</li>
            <li>Review detected changes</li>
            <li>Commit with firmware</li>
            <li>Scrub back through every version</li>
          </ol>
        </div>
        <HeroBoard />
      </section>

      <WorkflowSection />
      <CommandsSection />

      <div className="section-label">
        <h2>Projects</h2>
        <button type="button" className="btn primary" onClick={() => setCreating(true)}>
          New project
        </button>
      </div>

      {creating && <NewProjectModal onCreate={handleCreate} onClose={() => setCreating(false)} />}

      {projects === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <button type="button" className="project-card" onClick={() => onOpen(p)}>
                <span className="project-name">{p.name}</span>
                <span className="muted small">
                  {p.board.toUpperCase()} board, {p.updated ? `updated ${p.updated}` : 'no commits yet'}
                </span>
                {!p.setupComplete && <span className="chip warn">setup needed</span>}
              </button>
            </li>
          ))}
        </ul>
      )}

      <ExploreSection />
    </main>
  )
}

const HERO_STEP_MS = 1600

/** A small board that replays the build history on a loop (static at the latest commit for reduced motion). */
function HeroBoard() {
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [index, setIndex] = useState(0)
  const [allCircuits, setAllCircuits] = useState<Map<string, Circuit>>(new Map())

  // Bulk-fetch all circuits once so the animation never stalls on a per-step API call.
  useEffect(() => {
    let alive = true
    getTimeline().then(({ data }) => {
      if (!alive) return
      setEntries(data)
      setIndex(data.length - 1)
      Promise.all(data.map((e) => getCircuitAt(e.sha).then((c) => [e.sha, c] as const)))
        .then((pairs) => { if (alive) setAllCircuits(new Map(pairs)) })
        .catch(() => {})
    })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (entries.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const id = setInterval(() => setIndex((i) => (i + 1) % entries.length), HERO_STEP_MS)
    return () => clearInterval(id)
  }, [entries.length])

  const entry = entries[index]
  const circuit = entry ? allCircuits.get(entry.sha) : undefined
  const previous = index > 0 ? allCircuits.get(entries[index - 1]?.sha) ?? null : null

  return (
    <figure className="hero-board">
      <div className="board-frame">
        {circuit && <Breadboard circuit={circuit} previous={previous} />}
      </div>
      {entry && (
        <figcaption>
          <code>{entry.shortSha}</code>
          {entry.message}
        </figcaption>
      )}
    </figure>
  )
}
