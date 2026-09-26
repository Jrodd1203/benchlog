import { useState } from 'react'
import './App.css'
import { ProjectsScreen } from './screens/ProjectsScreen'
import { SetupScreen } from './screens/SetupScreen'
import { StubScreen } from './screens/StubScreen'
import { STUBS, type StubId } from './screens/stubs'
import { TimelineScreen } from './screens/TimelineScreen'
import { WorkspaceScreen } from './screens/WorkspaceScreen'
import type { ProjectSummary } from './types'

type Screen = 'projects' | 'setup' | 'workspace' | 'timeline' | StubId

/** Tabs shown once a project is open, in the team's requirement order. */
const TABS: { id: Exclude<Screen, 'projects'>; label: string }[] = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'review', label: 'Review' },
  { id: 'commits', label: 'Commits' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'diff', label: 'Diff' },
  { id: 'checks', label: 'Checks' },
  { id: 'github', label: 'GitHub' },
  { id: 'issues', label: 'Issues' },
  { id: 'serial', label: 'Serial' },
  { id: 'setup', label: 'Setup' },
]

export default function App() {
  const [screen, setScreen] = useState<Screen>('projects')
  const [project, setProject] = useState<ProjectSummary | null>(null)

  const open = (p: ProjectSummary) => {
    setProject(p)
    setScreen(p.setupComplete ? 'workspace' : 'setup')
  }

  return (
    <div className="app" data-screen={screen}>
      <nav className="topbar">
        <button type="button" className="brand" onClick={() => setScreen('projects')}>
          benchlog
        </button>
        {project && screen !== 'projects' && (
          <>
            <span className="crumb-sep">/</span>
            <span className="crumb current">{project.name}</span>
          </>
        )}
      </nav>

      {project && screen !== 'projects' && (
        <nav className="tabs" aria-label="Project sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`tab${screen === t.id ? ' active' : ''}`}
              aria-current={screen === t.id ? 'page' : undefined}
              onClick={() => setScreen(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      )}

      {screen === 'projects' && <ProjectsScreen onOpen={open} />}
      {screen === 'setup' && <SetupScreen onDone={() => setScreen('workspace')} />}
      {screen === 'workspace' && <WorkspaceScreen />}
      {screen === 'timeline' && <TimelineScreen />}
      {screen in STUBS && <StubScreen section={STUBS[screen as StubId]} />}
    </div>
  )
}
