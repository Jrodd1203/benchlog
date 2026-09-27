import { useState } from 'react'
import './App.css'
import { setActiveProject } from './api'
import { CommitsScreen } from './screens/CommitsScreen'
import { DiffScreen } from './screens/DiffScreen'
import { ProjectsScreen } from './screens/ProjectsScreen'
import { ReviewScreen } from './screens/ReviewScreen'
import { SerialScreen } from './screens/SerialScreen'
import { SetupScreen } from './screens/SetupScreen'
import { StubScreen } from './screens/StubScreen'
import { STUBS, type StubId } from './screens/stubs'
import { TimelineScreen } from './screens/TimelineScreen'
import { WorkspaceScreen } from './screens/WorkspaceScreen'
import type { ProjectSummary } from './types'

type Screen = 'projects' | 'setup' | 'workspace' | 'review' | 'commits' | 'timeline' | 'diff' | 'serial' | StubId

/** Tabs shown once a project is open — only built screens. */
const TABS: { id: Exclude<Screen, 'projects'>; label: string }[] = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'setup', label: 'Setup' },
]

export default function App() {
  const [screen, setScreen] = useState<Screen>('projects')
  const [project, setProject] = useState<ProjectSummary | null>(null)

  const open = (p: ProjectSummary) => {
    setProject(p)
    setActiveProject(p.id)
    setScreen(p.setupComplete ? 'workspace' : 'setup')
  }

  const backToProjects = () => {
    setActiveProject(null)
    setProject(null)
    setScreen('projects')
  }

  return (
    <div className="app" data-screen={screen}>
      <nav className="topbar">
        <button type="button" className="brand" onClick={backToProjects}>
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
      {screen === 'workspace' && <WorkspaceScreen onReview={() => setScreen('review')} />}
      {screen === 'review' && (
        <ReviewScreen onCommit={() => setScreen('commits')} onWorkspace={() => setScreen('workspace')} />
      )}
      {screen === 'commits' && (
        <CommitsScreen onReview={() => setScreen('review')} onTimeline={() => setScreen('timeline')} />
      )}
      {screen === 'timeline' && <TimelineScreen />}
      {screen === 'diff' && <DiffScreen />}
      {screen === 'serial' && <SerialScreen />}
      {screen in STUBS && <StubScreen section={STUBS[screen as StubId]} />}
    </div>
  )
}
