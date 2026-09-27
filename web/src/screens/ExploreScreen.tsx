import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchCommunityRepos, fetchRemoteCircuitAt, fetchRemoteHistory, parseRepo } from '../api'
import { Breadboard } from '../board/Breadboard'
import { ComponentIcon, componentLabel } from '../components/ComponentIcon'
import { describeDiff, diffCircuits } from '../diff'
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
  const [community, setCommunity] = useState<RemoteRepo[] | null>(null)

  useEffect(() => {
    fetchCommunityRepos().then(setCommunity)
  }, [])

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
    return <RepoList repos={REPOS} recent={recent} community={community} onOpen={openRepo} onOpenByName={(o, r) => openRepo(repoFromName(o, r))} />
  }
  if (view.kind === 'loading') return <LoadingPane label={`Loading ${view.repo.label}…`} onBack={back} />
  if (view.kind === 'error') return <ErrorPane repo={view.repo} message={view.message} onBack={back} />
  return <RemoteTimeline repo={view.repo} commits={view.commits} onBack={back} />
}

// ── Repo list ─────────────────────────────────────────────────────────────────

function RepoList({
  repos,
  recent,
  community,
  onOpen,
  onOpenByName,
}: {
  repos: RemoteRepo[]
  recent: RemoteRepo[]
  community: RemoteRepo[] | null
  onOpen: (r: RemoteRepo) => void
  onOpenByName: (owner: string, repo: string) => void
}) {
  const [input, setInput] = useState('')
  const [inputError, setInputError] = useState<string | null>(null)

  const featured = new Set(repos.map((r) => `${r.owner}/${r.repo}`.toLowerCase()))
  const communityOnly = (community ?? []).filter((r) => !featured.has(`${r.owner}/${r.repo}`.toLowerCase()))

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
        </>
      )}
      <h2 className="explore-section">Community builds</h2>
      {community === null ? (
        <p className="muted">Looking for builds…</p>
      ) : communityOnly.length === 0 ? (
        <p className="muted">
          None yet. Publish yours with <code>benchlog push --create my-circuit</code>
        </p>
      ) : (
        <div className="explore-grid">{communityOnly.map((r) => <RepoCard key={`${r.owner}/${r.repo}`} repo={r} onOpen={onOpen} />)}</div>
      )}
      <h2 className="explore-section">Featured</h2>
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

// ── A repo: Timeline, Diff, Commits ───────────────────────────────────────────
// Everything here is read-only and comes from GitHub: the commit list (one request, cached) and
// benchlog/circuit.json at each commit (cached for good). Diffs are computed in the browser.

type RepoTab = 'timeline' | 'diff' | 'commits'

const REPO_TABS: { id: RepoTab; label: string }[] = [
  { id: 'timeline', label: 'Timeline' },
  { id: 'diff', label: 'Diff' },
  { id: 'commits', label: 'Commits' },
]

/** Circuits by commit sha, fetched once and shared by every tab. */
function useCircuits(repo: RemoteRepo) {
  const cache = useRef<Map<string, Promise<Circuit | null>>>(new Map())
  // Stable across renders: the tabs load circuits in effects that depend on it.
  return useCallback(
    (sha: string): Promise<Circuit | null> => {
      let pending = cache.current.get(sha)
      if (!pending) {
        pending = fetchRemoteCircuitAt(repo.owner, repo.repo, sha)
        cache.current.set(sha, pending)
      }
      return pending
    },
    [repo.owner, repo.repo],
  )
}

function RemoteTimeline({ repo, commits, onBack }: { repo: RemoteRepo; commits: RemoteCommit[]; onBack: () => void }) {
  const [tab, setTab] = useState<RepoTab>('timeline')
  const [index, setIndex] = useState(commits.length - 1) // open on the latest build
  const circuitAt = useCircuits(repo)

  const openCommit = (i: number) => {
    setIndex(i)
    setTab('timeline')
  }

  return (
    <div className="timeline">
      <div className="timeline-mode-bar">
        <button type="button" className="btn explore-back" onClick={onBack}>← Explore</button>
        <span style={{ fontWeight: 600 }}>{repo.label}</span>
        <a className="muted sha-link" href={`https://github.com/${repo.owner}/${repo.repo}`} target="_blank" rel="noreferrer">
          {repo.owner}/{repo.repo}
        </a>
      </div>
      <nav className="tabs explore-tabs" aria-label="Repository sections">
        {REPO_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`tab${tab === t.id ? ' active' : ''}`}
            aria-current={tab === t.id ? 'page' : undefined}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === 'commits' && <span className="tab-count">{commits.length}</span>}
          </button>
        ))}
      </nav>
      {tab === 'timeline' && <TimelineTab repo={repo} commits={commits} index={index} setIndex={setIndex} circuitAt={circuitAt} />}
      {tab === 'diff' && <DiffTab commits={commits} circuitAt={circuitAt} initialNew={index} />}
      {tab === 'commits' && <CommitsTab repo={repo} commits={commits} onOpen={openCommit} />}
    </div>
  )
}

// ── Timeline tab ──────────────────────────────────────────────────────────────

function TimelineTab({
  repo,
  commits,
  index,
  setIndex,
  circuitAt,
}: {
  repo: RemoteRepo
  commits: RemoteCommit[]
  index: number
  setIndex: (update: number | ((i: number) => number)) => void
  circuitAt: (sha: string) => Promise<Circuit | null>
}) {
  const [boards, setBoards] = useState<{ circuit: Circuit; previous: Circuit | null } | null>(null)
  const [circuitError, setCircuitError] = useState(false)

  useEffect(() => {
    let alive = true
    setBoards(null)
    setCircuitError(false)
    const previous = index > 0 ? circuitAt(commits[index - 1].sha) : Promise.resolve(null)
    Promise.all([circuitAt(commits[index].sha), previous]).then(([circuit, prev]) => {
      if (!alive) return
      if (!circuit) setCircuitError(true)
      else setBoards({ circuit, previous: prev })
    })
    return () => {
      alive = false
    }
  }, [index, commits, circuitAt])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1))
      else if (e.key === 'ArrowRight') setIndex((i) => Math.min(commits.length - 1, i + 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [commits.length, setIndex])

  const commit = commits[index]
  const changes = boards ? describeDiff(diffCircuits(boards.previous ?? emptyLike(boards.circuit), boards.circuit)) : null

  return (
    <>
      <div className="explore-board-row">
        <div className="board-frame">
          {boards && <Breadboard circuit={boards.circuit} previous={boards.previous} />}
          {!boards && !circuitError && <span className="muted" style={{ fontSize: '0.85rem' }}>Loading circuit…</span>}
          {circuitError && <span className="muted" style={{ fontSize: '0.85rem' }}>Could not load circuit.json at this commit.</span>}
        </div>
        <CircuitPanel circuit={boards?.circuit ?? null} />
      </div>

      <div className="scrubber">
        <button type="button" className="btn icon" onClick={() => setIndex(0)} disabled={index === 0} title="First">⏮</button>
        <button type="button" className="btn icon" onClick={() => setIndex((i) => Math.max(0, i - 1))} disabled={index === 0} title="Previous (←)">‹</button>
        <div className="scrub-track">
          <input type="range" min={0} max={commits.length - 1} value={index} onChange={(e) => setIndex(Number(e.target.value))} />
        </div>
        <button type="button" className="btn icon" onClick={() => setIndex((i) => Math.min(commits.length - 1, i + 1))} disabled={index === commits.length - 1} title="Next (→)">›</button>
        <button type="button" className="btn icon" onClick={() => setIndex(commits.length - 1)} disabled={index === commits.length - 1} title="Last">⏭</button>
        <span className="scrub-count">{index + 1} / {commits.length}</span>
        <span className="muted" style={{ fontSize: '0.75rem', marginLeft: 'auto' }}>Arrow keys step</span>
      </div>

      <div className={`commit-info${changes?.some(isElectrical) ? ' electrical' : ''}`}>
        <div className="commit-head">
          <h2>{commit.message}</h2>
          <CheckStamp check={commit.check} />
        </div>
        <p className="commit-meta">
          <a href={`https://github.com/${repo.owner}/${repo.repo}/commit/${commit.sha}`} target="_blank" rel="noreferrer" className="sha-link">
            {commit.shortSha}
          </a>{' '}
          {commit.author}, {new Date(commit.date).toLocaleString()}
        </p>
        {changes && <ChangeLines lines={changes} empty={index === 0 ? 'The first build.' : 'No circuit changes in this commit.'} />}
      </div>
    </>
  )
}

/** An empty board of the same kind: what the first commit is compared with. */
function emptyLike(circuit: Circuit): Circuit {
  return { ...circuit, components: [], wires: [] }
}

function CircuitPanel({ circuit }: { circuit: Circuit | null }) {
  return (
    <aside className="side-panel">
      <h2>Circuit</h2>
      {circuit === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <dl className="facts">
          <dt>Board</dt>
          <dd>{circuit.board.toUpperCase()}</dd>
          <dt>Parts</dt>
          <dd>
            {circuit.components.length === 0 ? (
              <span className="muted">none</span>
            ) : (
              <ul className="component-icon-list">
                {circuit.components.map((c) => (
                  <li key={c.id} className="component-icon-item" title={`${c.id} — ${c.value ?? componentLabel(c.type)}`}>
                    <ComponentIcon type={c.type} size={28} />
                    <span className="component-icon-label">{c.id}</span>
                  </li>
                ))}
              </ul>
            )}
          </dd>
          <dt>Wires</dt>
          <dd>{circuit.wires.length}</dd>
        </dl>
      )}
    </aside>
  )
}

// ── Diff tab ──────────────────────────────────────────────────────────────────

function DiffTab({
  commits,
  circuitAt,
  initialNew,
}: {
  commits: RemoteCommit[]
  circuitAt: (sha: string) => Promise<Circuit | null>
  initialNew: number
}) {
  const [newIndex, setNewIndex] = useState(initialNew)
  const [oldIndex, setOldIndex] = useState(Math.max(0, initialNew - 1))
  const [boards, setBoards] = useState<{ before: Circuit; after: Circuit } | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    setBoards(null)
    setFailed(false)
    Promise.all([circuitAt(commits[oldIndex].sha), circuitAt(commits[newIndex].sha)]).then(([before, after]) => {
      if (!alive) return
      if (!before || !after) setFailed(true)
      else setBoards({ before, after })
    })
    return () => {
      alive = false
    }
  }, [oldIndex, newIndex, commits, circuitAt])

  const options = [...commits.keys()].reverse().map((i) => (
    <option key={commits[i].sha} value={i}>
      {commits[i].shortSha} {commits[i].message}
    </option>
  ))
  const lines = boards ? describeDiff(diffCircuits(boards.before, boards.after)) : null

  return (
    <>
      <div className="scrubber diff-pickers">
        <label className="field">
          <span>From</span>
          <select value={oldIndex} onChange={(e) => setOldIndex(Number(e.target.value))}>{options}</select>
        </label>
        <label className="field">
          <span>To</span>
          <select value={newIndex} onChange={(e) => setNewIndex(Number(e.target.value))}>{options}</select>
        </label>
      </div>
      <div className="board-frame">
        {boards && <Breadboard circuit={boards.after} previous={boards.before} />}
        {!boards && !failed && <span className="muted" style={{ fontSize: '0.85rem' }}>Loading circuits…</span>}
        {failed && <span className="muted" style={{ fontSize: '0.85rem' }}>Could not load circuit.json at one of these commits.</span>}
      </div>
      <section className="commit-info">
        <h2>What changed</h2>
        {lines === null ? <p className="muted">Loading…</p> : <ChangeLines lines={lines} empty="These two versions are the same circuit." />}
        <p className="muted small">⚡ marks electrical changes; the rest only moved to a different hole on the same strip, or changed a label.</p>
      </section>
    </>
  )
}

const isElectrical = (line: string) => /\b(connected|disconnected)\b/.test(line)

function ChangeLines({ lines, empty }: { lines: string[]; empty: string }) {
  if (lines.length === 0) return <p className="muted">{empty}</p>
  return (
    <ul className="change-lines">
      {lines.map((l) => (
        <li key={l} className={isElectrical(l) ? 'electrical' : undefined}>
          {isElectrical(l) ? '⚡ ' : ''}
          {l}
        </li>
      ))}
    </ul>
  )
}

// ── Commits tab ───────────────────────────────────────────────────────────────

function CommitsTab({ repo, commits, onOpen }: { repo: RemoteRepo; commits: RemoteCommit[]; onOpen: (index: number) => void }) {
  return (
    <ol className="explore-commits">
      {[...commits.keys()].reverse().map((i) => {
        const c = commits[i]
        return (
          <li key={c.sha}>
            <button type="button" className="explore-commit" onClick={() => onOpen(i)} title="Show this build on the timeline">
              <span className="explore-commit-message">{c.message}</span>
              <span className="muted small">
                {c.author}, {new Date(c.date).toLocaleString()}
              </span>
            </button>
            <CheckStamp check={c.check} />
            <a href={`https://github.com/${repo.owner}/${repo.repo}/commit/${c.sha}`} target="_blank" rel="noreferrer" className="sha-link">
              {c.shortSha}
            </a>
          </li>
        )
      })}
    </ol>
  )
}

/** The ESP32 check `benchlog commit` ran before this commit. Skipped is never shown as a pass. */
function CheckStamp({ check }: { check: RemoteCommit['check'] }) {
  if (!check) return <span className="badge check-none" title="Committed without an ESP32 check result">no check</span>
  const label = { passed: 'ESP32 check passed', failed: 'ESP32 check failed', skipped: 'ESP32 not checked' }[check.status]
  return (
    <span className={`badge check-${check.status}`} title={check.forced ? 'Committed anyway with --force' : undefined}>
      {label}
      {check.forced ? ' (forced)' : ''}
    </span>
  )
}
