import { useEffect, useRef, useState } from 'react'
import { fetchRemoteCircuitAt, fetchRemoteHistory, parseRepo } from '../api'
import { Breadboard } from '../board/Breadboard'
import type { Circuit, RemoteCommit, RemoteRepo } from '../types'

// ── Curated repo list ─────────────────────────────────────────────────────────
// Add repos here as builds go up. owner/repo must be public GitHub repos that
// have benchlog/circuit.json committed. Label and description are display-only.

const REPOS: RemoteRepo[] = [
  {
    owner: 'ctrl-alt-debrief',
    repo: 'benchlog-pot-led',
    label: 'Pot + LED',
    description: 'ESP32 potentiometer reading with status LED on GPIO13',
  },
  {
    owner: 'ctrl-alt-debrief',
    repo: 'benchlog-weather',
    label: 'Weather Station',
    description: 'ESP32 + BME280 temperature/humidity sensor over I²C',
  },
  {
    owner: 'ctrl-alt-debrief',
    repo: 'benchlog-led-bar',
    label: 'LED Bar',
    description: 'Three-LED bar graph driven by GPIO12, GPIO13, GPIO14',
  },
]

// ── Repos people opened by name (kept in this browser) ────────────────────────

const RECENT_KEY = 'benchlog:explore:recent'
const MAX_RECENT = 6

function loadRecent(): RemoteRepo[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY)
    return raw ? (JSON.parse(raw) as RemoteRepo[]) : []
  } catch {
    return []
  }
}

function saveRecent(repo: RemoteRepo, current: RemoteRepo[]): RemoteRepo[] {
  const same = (r: RemoteRepo) => `${r.owner}/${r.repo}`.toLowerCase() === `${repo.owner}/${repo.repo}`.toLowerCase()
  if (REPOS.some(same)) return current // curated ones are always listed
  const next = [repo, ...current.filter((r) => !same(r))].slice(0, MAX_RECENT)
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    // storage unavailable: the list just won't survive a reload
  }
  return next
}

function repoFromName(owner: string, repo: string): RemoteRepo {
  const known = REPOS.find((r) => `${r.owner}/${r.repo}`.toLowerCase() === `${owner}/${repo}`.toLowerCase())
  return known ?? { owner, repo, label: repo, description: 'Opened by name' }
}

// ── Types ─────────────────────────────────────────────────────────────────────

type ViewState =
  | { kind: 'list' }
  | { kind: 'loading'; repo: RemoteRepo }
  | { kind: 'error'; repo: RemoteRepo; message: string }
  | { kind: 'timeline'; repo: RemoteRepo; commits: RemoteCommit[] }

// ── Component ─────────────────────────────────────────────────────────────────

export function ExploreScreen() {
  const [view, setView] = useState<ViewState>({ kind: 'list' })
  const [recent, setRecent] = useState<RemoteRepo[]>(loadRecent)

  const openRepo = async (repo: RemoteRepo) => {
    setRecent((current) => saveRecent(repo, current))
    setView({ kind: 'loading', repo })
    try {
      const commits = await fetchRemoteHistory(repo.owner, repo.repo)
      if (commits.length === 0) {
        setView({ kind: 'error', repo, message: 'No circuit commits found in this repo yet.' })
        return
      }
      // API returns newest-first; reverse so index 0 is the first build step.
      setView({ kind: 'timeline', repo, commits: [...commits].reverse() })
    } catch (err) {
      setView({ kind: 'error', repo, message: err instanceof Error ? err.message : 'Failed to load repo.' })
    }
  }

  const back = () => setView({ kind: 'list' })

  // A shareable link opens a repo directly: ...?repo=owner/repo
  useEffect(() => {
    const wanted = parseRepo(new URLSearchParams(window.location.search).get('repo') ?? '')
    if (wanted) openRepo(repoFromName(wanted.owner, wanted.repo))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (view.kind === 'list') {
    return <RepoList repos={REPOS} recent={recent} onOpen={openRepo} onOpenByName={(o, r) => openRepo(repoFromName(o, r))} />
  }
  if (view.kind === 'loading') return <LoadingPane label={`Loading ${view.repo.label}…`} onBack={back} />
  if (view.kind === 'error') return <ErrorPane repo={view.repo} message={view.message} onBack={back} />
  return <RemoteTimeline repo={view.repo} commits={view.commits} onBack={back} />
}

// ── Repo list ─────────────────────────────────────────────────────────────────

function RepoList({
  repos,
  recent,
  onOpen,
  onOpenByName,
}: {
  repos: RemoteRepo[]
  recent: RemoteRepo[]
  onOpen: (r: RemoteRepo) => void
  onOpenByName: (owner: string, repo: string) => void
}) {
  const [input, setInput] = useState('')
  const [inputError, setInputError] = useState<string | null>(null)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const parsed = parseRepo(input)
    if (!parsed) {
      setInputError('Enter owner/repo or a GitHub link, e.g. ctrl-alt-debrief/benchlog-pot-led')
      return
    }
    setInputError(null)
    onOpenByName(parsed.owner, parsed.repo)
  }

  return (
    <div className="explore-root">
      <div className="explore-header">
        <h1>Explore</h1>
        <p className="explore-sub">Public builds from the community. No sign-in required.</p>
      </div>
      <form className="explore-open" onSubmit={submit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Open any public repo: owner/repo or a GitHub link"
          aria-label="GitHub repository"
          spellCheck={false}
        />
        <button type="submit" className="btn primary">Open</button>
      </form>
      {inputError && <p className="explore-open-error">{inputError}</p>}
      {recent.length > 0 && (
        <>
          <h2 className="explore-section">Recently opened</h2>
          <div className="explore-grid">{recent.map((r) => <RepoCard key={`${r.owner}/${r.repo}`} repo={r} onOpen={onOpen} />)}</div>
          <h2 className="explore-section">Featured</h2>
        </>
      )}
      <div className="explore-grid">
        {repos.map((r) => <RepoCard key={`${r.owner}/${r.repo}`} repo={r} onOpen={onOpen} />)}
      </div>
    </div>
  )
}

function RepoCard({ repo, onOpen }: { repo: RemoteRepo; onOpen: (r: RemoteRepo) => void }) {
  return (
    <button type="button" className="explore-card" onClick={() => onOpen(repo)}>
      <div className="explore-card-label">{repo.label}</div>
      <div className="explore-card-repo">{repo.owner}/{repo.repo}</div>
      <div className="explore-card-desc">{repo.description}</div>
      <div className="explore-card-cta">View history →</div>
    </button>
  )
}

// ── Loading / error panes ─────────────────────────────────────────────────────

function LoadingPane({ label, onBack }: { label: string; onBack: () => void }) {
  return (
    <div className="explore-root">
      <button type="button" className="btn explore-back" onClick={onBack}>← Back</button>
      <div className="explore-status">{label}</div>
    </div>
  )
}

function ErrorPane({ repo, message, onBack }: { repo: RemoteRepo; message: string; onBack: () => void }) {
  return (
    <div className="explore-root">
      <button type="button" className="btn explore-back" onClick={onBack}>← Back</button>
      <div className="explore-status">
        <strong>{repo.label}</strong>
        <p className="muted">{message}</p>
      </div>
    </div>
  )
}

// ── Remote timeline ───────────────────────────────────────────────────────────

function RemoteTimeline({ repo, commits, onBack }: { repo: RemoteRepo; commits: RemoteCommit[]; onBack: () => void }) {
  const [index, setIndex] = useState(0)
  const [boards, setBoards] = useState<{ circuit: Circuit; previous: Circuit | null } | null>(null)
  const [circuitError, setCircuitError] = useState(false)

  // Cache fetched circuits so scrubbing doesn't re-fetch.
  const cache = useRef<Map<string, Circuit | null>>(new Map())

  const getCircuit = async (sha: string): Promise<Circuit | null> => {
    if (cache.current.has(sha)) return cache.current.get(sha) ?? null
    const c = await fetchRemoteCircuitAt(repo.owner, repo.repo, sha)
    cache.current.set(sha, c)
    return c
  }

  useEffect(() => {
    let alive = true
    setBoards(null)
    setCircuitError(false)
    const commit = commits[index]
    const prevCommit = index > 0 ? commits[index - 1] : null
    Promise.all([getCircuit(commit.sha), prevCommit ? getCircuit(prevCommit.sha) : Promise.resolve(null)]).then(
      ([circuit, previous]) => {
        if (!alive) return
        if (!circuit) { setCircuitError(true); return }
        setBoards({ circuit, previous })
      },
    )
    return () => { alive = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, commits, repo])

  // Keyboard nav
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1))
      else if (e.key === 'ArrowRight') setIndex((i) => Math.min(commits.length - 1, i + 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [commits.length])

  const commit = commits[index]
  const date = new Date(commit.date).toLocaleString()

  return (
    <div className="timeline">
      {/* Mode bar reused from timeline */}
      <div className="timeline-mode-bar">
        <button type="button" className="btn explore-back" onClick={onBack}>← Explore</button>
        <span style={{ fontWeight: 600 }}>{repo.label}</span>
        <span className="muted" style={{ fontSize: '0.8rem' }}>{repo.owner}/{repo.repo}</span>
      </div>

      {/* Board */}
      <div className="board-frame">
        {boards && !circuitError && <Breadboard circuit={boards.circuit} previous={boards.previous} />}
        {!boards && !circuitError && <span className="muted" style={{ fontSize: '0.85rem' }}>Loading circuit…</span>}
        {circuitError && <span className="muted" style={{ fontSize: '0.85rem' }}>Could not load circuit.json at this commit.</span>}
      </div>

      {/* Scrubber */}
      <div className="scrubber">
        <button type="button" className="btn icon" onClick={() => setIndex(0)} disabled={index === 0} title="First">⏮</button>
        <button type="button" className="btn icon" onClick={() => setIndex((i) => Math.max(0, i - 1))} disabled={index === 0} title="Previous (←)">‹</button>
        <div className="scrub-track">
          <input
            type="range"
            min={0}
            max={commits.length - 1}
            value={index}
            onChange={(e) => setIndex(Number(e.target.value))}
          />
        </div>
        <button type="button" className="btn icon" onClick={() => setIndex((i) => Math.min(commits.length - 1, i + 1))} disabled={index === commits.length - 1} title="Next (→)">›</button>
        <button type="button" className="btn icon" onClick={() => setIndex(commits.length - 1)} disabled={index === commits.length - 1} title="Last">⏭</button>
        <span className="scrub-count">{index + 1} / {commits.length}</span>
        <span className="muted" style={{ fontSize: '0.75rem', marginLeft: 'auto' }}>Arrow keys step</span>
      </div>

      {/* Commit info */}
      <div className="commit-info">
        <div className="commit-head">
          <h2>{commit.message}</h2>
        </div>
        <p className="commit-meta">
          <a
            href={`https://github.com/${repo.owner}/${repo.repo}/commit/${commit.sha}`}
            target="_blank"
            rel="noreferrer"
            className="sha-link"
          >
            {commit.shortSha}
          </a>
          {' '}{commit.author}, {date}
        </p>
      </div>
    </div>
  )
}
