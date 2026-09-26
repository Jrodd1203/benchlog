import { useEffect, useState } from 'react'
import { listProjects } from '../api'
import { FutureButton } from '../components/FutureButton'
import type { ProjectSummary } from '../types'

/** Requirement 1: list local hardware projects, create one, or open one. */
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
    <main className="page narrow">
      <header className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="muted">Hardware projects saved on this computer.</p>
        </div>
        <FutureButton primary needs="POST /api/projects (create a project folder + git repo)">
          New project
        </FutureButton>
      </header>

      {projects === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <button type="button" className="project-card" onClick={() => onOpen(p)}>
                <span className="project-name">{p.name}</span>
                <span className="muted small">
                  {p.board.toUpperCase()} · {p.updated ? `updated ${p.updated}` : 'no commits yet'}
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
