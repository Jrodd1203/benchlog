import { useEffect, useState } from 'react'
import { listProjects } from '../api'
import { FutureButton } from '../components/FutureButton'
import type { ProjectSummary } from '../types'

/** Requirement 1 (landing page): what benchlog is, then the local projects to open. */
export function ProjectsScreen({ onOpen }: { onOpen: (p: ProjectSummary) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)

  useEffect(() => {
    let alive = true
    listProjects().then((p) => {
      if (alive) setProjects(p)
    })
    return () => {
      alive = false
    }
  }, [])

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
        <HeroCodePanel />
      </section>

      <CommandsSection />

      <div className="section-label">
        <h2>Projects</h2>
        <FutureButton primary needs="POST /api/projects (create a project folder + git repo)">
          New project
        </FutureButton>
      </div>

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

      <div className="row">
        <FutureButton needs="a folder picker + POST /api/projects/open">Open existing folder…</FutureButton>
      </div>
    </main>
  )
}

// ── Tabbed code panel ─────────────────────────────────────────────────────────

type TabId = 'scan' | 'review' | 'commit' | 'check'
type LineType = 'prompt' | 'ok' | 'bad' | 'warn' | 'comment' | 'plain' | 'empty'

interface CodeLine {
  type: LineType
  text: string
}

const TAB_CONTENT: Record<TabId, CodeLine[]> = {
  scan: [
    { type: 'prompt', text: '$ benchlog scan' },
    { type: 'empty', text: '' },
    { type: 'comment', text: '⠸ Reading board with camera…' },
    { type: 'empty', text: '' },
    { type: 'warn', text: '  ~ w5: A4 → A12  (moved)' },
    { type: 'warn', text: '  ~ w5: now connected to GPIO12 (strapping pin)' },
    { type: 'empty', text: '' },
    { type: 'comment', text: '2 changes detected. Run `benchlog review` to accept.' },
  ],
  review: [
    { type: 'prompt', text: '$ benchlog review' },
    { type: 'empty', text: '' },
    { type: 'warn', text: '  ~ w5: A4 → A12  (moved)' },
    { type: 'comment', text: '    w5 is now connected to GPIO12 — a strapping pin.' },
    { type: 'comment', text: '    This can prevent the ESP32 from booting.' },
    { type: 'empty', text: '' },
    { type: 'comment', text: '  [a] accept  [r] reject  [s] skip  [q] quit' },
    { type: 'empty', text: '' },
    { type: 'plain', text: '> a' },
    { type: 'ok', text: '✓ accepted: w5 moved to A12' },
  ],
  commit: [
    { type: 'prompt', text: '$ benchlog commit -m "Move pot signal to GPIO12"' },
    { type: 'empty', text: '' },
    { type: 'plain', text: '[main 4f2a1b3] Move pot signal to GPIO12' },
    { type: 'plain', text: ' 1 file changed, 2 insertions(+), 1 deletion(-)' },
    { type: 'empty', text: '' },
    { type: 'ok', text: '✓ Circuit snapshot saved alongside firmware.' },
  ],
  check: [
    { type: 'prompt', text: '$ benchlog check' },
    { type: 'empty', text: '' },
    { type: 'bad', text: '  ✗ w5 is connected to GPIO12 — a strapping pin.' },
    { type: 'comment', text: '    ESP32 may not boot. Move the wire to a safe GPIO.' },
    { type: 'empty', text: '' },
    { type: 'comment', text: '1 check failed.' },
  ],
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'scan', label: 'Scan' },
  { id: 'review', label: 'Review' },
  { id: 'commit', label: 'Commit' },
  { id: 'check', label: 'Check' },
]

function HeroCodePanel() {
  const [activeTab, setActiveTab] = useState<TabId>('scan')
  const lines = TAB_CONTENT[activeTab]

  return (
    <div className="hero-code-panel">
      <div className="hero-code-tabbar">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`hero-code-tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="hero-code-body">
        {lines.map((line, i) => (
          <div key={i} className="term-line">
            <span className="term-lnum">{i + 1}</span>
            {line.type === 'empty' ? (
              <span className="term-text">{' '}</span>
            ) : line.type === 'prompt' ? (
              <span className="term-text">
                <span className="term-prompt">$</span>
                {line.text.slice(1)}
              </span>
            ) : (
              <span className={`term-text term-${line.type}`}>{line.text}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── CLI commands reference ────────────────────────────────────────────────────

const COMMANDS = [
  { name: 'init',   desc: 'Create a benchlog project in the current git repo' },
  { name: 'scan',   desc: 'Read the board with the camera; detect wire changes' },
  { name: 'review', desc: 'Interactively accept or reject each detected change' },
  { name: 'status', desc: 'Show the current circuit vs the last commit' },
  { name: 'diff',   desc: 'Show changes between two commits (or HEAD vs last)' },
  { name: 'commit', desc: 'Save the current circuit + firmware as a new commit' },
  { name: 'log',    desc: 'List commits with their circuit change summary' },
  { name: 'check',  desc: 'Run electrical safety checks on the current circuit' },
  { name: 'camera', desc: 'Manage camera selection and calibration' },
] as const

const OPTIONS = [
  { name: '--help',    desc: 'Show this message and exit.' },
  { name: '--version', desc: 'Show the version and exit.' },
] as const

function CommandsSection() {
  return (
    <section className="commands-section">
      <div className="section-label">
        <h2>Commands</h2>
      </div>
      <div className="commands-panel">
        <div className="commands-panel-header">
          <span className="term-prompt">$</span>
          {' benchlog --help'}
        </div>
        <div className="commands-body">
          <div className="cmd-line">{' '}</div>
          <div className="cmd-line">{'  Version control for breadboard prototypes.'}</div>
          <div className="cmd-line">{' '}</div>
          <div className="cmd-line">{'  Commands:'}</div>
          {COMMANDS.map((cmd) => (
            <div key={cmd.name} className="cmd-line">
              {'    '}
              <span className="cmd-name">{cmd.name.padEnd(10)}</span>
              {cmd.desc}
            </div>
          ))}
          <div className="cmd-line">{' '}</div>
          <div className="cmd-line">{'  Options:'}</div>
          {OPTIONS.map((opt) => (
            <div key={opt.name} className="cmd-line">
              {'    '}
              <span className="cmd-name">{opt.name.padEnd(10)}</span>
              {opt.desc}
            </div>
          ))}
          <div className="cmd-line">{' '}</div>
        </div>
      </div>
    </section>
  )
}
