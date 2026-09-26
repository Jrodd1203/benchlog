import { useEffect, useState } from 'react'
import { getCircuitAt, getTimeline, listProjects } from '../api'
import { Breadboard } from '../board/Breadboard'
import { FutureButton } from '../components/FutureButton'
import type { Circuit, ProjectSummary, TimelineEntry } from '../types'

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
        <HeroBoard />
      </section>

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
      <p className="note">
        The project list is placeholder data until the backend can list projects (GET /api/projects).
      </p>
    </main>
  )
}

const HERO_STEP_MS = 1600

/** A small board that replays the build history on a loop (static at the latest commit for reduced motion). */
function HeroBoard() {
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [index, setIndex] = useState(0)
  const [boards, setBoards] = useState<{ circuit: Circuit; previous: Circuit | null } | null>(null)

  useEffect(() => {
    let alive = true
    getTimeline().then(({ data }) => {
      if (!alive) return
      setEntries(data)
      setIndex(data.length - 1)
    })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (entries.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const id = setInterval(() => setIndex((i) => (i + 1) % entries.length), HERO_STEP_MS)
    return () => clearInterval(id)
  }, [entries.length])

  useEffect(() => {
    if (entries.length === 0) return
    let alive = true
    const prev = index > 0 ? entries[index - 1].sha : null
    Promise.all([getCircuitAt(entries[index].sha), prev ? getCircuitAt(prev) : null]).then(([circuit, previous]) => {
      if (alive) setBoards({ circuit, previous })
    })
    return () => {
      alive = false
    }
  }, [entries, index])

  const entry = entries[index]
  return (
    <figure className="hero-board">
      <div className="board-frame">{boards && <Breadboard circuit={boards.circuit} previous={boards.previous} />}</div>
      {entry && (
        <figcaption>
          <span>
            <code>{entry.shortSha}</code>
            {entry.message}
          </span>
          <span>
            {index + 1} of {entries.length}
          </span>
        </figcaption>
      )}
    </figure>
  )
}
