import { useCallback, useEffect, useState } from 'react'
import { getCircuit } from '../api'
import {
  checkout,
  closePR,
  createBranch,
  createPR,
  getBoardState,
  getPR,
  listBranches,
  listPRs,
  markTested,
  mergePR,
  shortSha,
  type BoardReport,
  type Branch,
  type PR,
  type PRDetail,
  type RewireStep,
} from '../api/prs'
import { Breadboard } from '../board/Breadboard'
import { CheckBadge } from '../components/CheckBadge'
import { RewireGuide } from '../components/RewireGuide'
import type { Circuit } from '../types'
import './prs.css'

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

const fetchAll = () => Promise.all([listBranches(), getBoardState(), listPRs(), getCircuit()])

/** Requirement 10 (local): branches, the board-state guide, and pull requests with checks. */
export function PullRequestsScreen() {
  const [branches, setBranches] = useState<Branch[] | null>(null)
  const [board, setBoard] = useState<BoardReport | null>(null)
  const [circuit, setCircuit] = useState<Circuit | null>(null)
  const [prs, setPrs] = useState<PR[] | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const show = useCallback(([b, s, p, c]: Awaited<ReturnType<typeof fetchAll>>) => {
    setBranches(b)
    setBoard(s)
    setPrs(p)
    setCircuit(c.data)
  }, [])

  const refresh = useCallback(async () => {
    try {
      show(await fetchAll())
    } catch (e) {
      setError(message(e))
    }
  }, [show])

  useEffect(() => {
    let alive = true
    fetchAll()
      .then((data) => alive && show(data))
      .catch((e) => alive && setError(message(e)))
    return () => {
      alive = false
    }
  }, [show])

  /** Run an action, show its error (the server's `detail` is written for the user), then reload. */
  const act = async (fn: () => Promise<string | void>) => {
    setError(null)
    setNotice(null)
    try {
      const done = await fn()
      if (done) setNotice(done)
    } catch (e) {
      setError(message(e))
    }
    await refresh()
  }

  const current = branches?.find((b) => b.current)?.name

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1>Branches &amp; PRs</h1>
          <p className="muted">
            Try a change on a branch, check it, and merge it into main. Everything stays local: git branches plus PR
            files in <code>.benchlog/prs/</code>.
          </p>
        </div>
        {current && <span className="chip">on {current}</span>}
      </header>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="notice ok">{notice}</p>}

      <div className="stack">
        {board && circuit && <BoardPanel board={board} circuit={circuit} />}

        <div className="split">
          <BranchesPanel branches={branches} act={act} />
          <PRListPanel prs={prs} current={current} selectedId={selectedId} onSelect={setSelectedId} act={act} />
        </div>

        {selectedId !== null && (
          <PRDetailPanel key={selectedId} id={selectedId} act={act} />
        )}
      </div>
    </main>
  )
}

// ── Board state ───────────────────────────────────────────────────────────────────────────────

function BoardPanel({ board, circuit }: { board: BoardReport; circuit: Circuit }) {
  const holes = board.guide.flatMap((s) => s.holes)
  return (
    <section className="panel">
      <h2>Physical board</h2>
      {board.matches === true && <p className="notice ok">The board matches {board.branch}. Nothing to rewire.</p>}
      {board.matches === null && (
        <p className="notice warn">benchlog hasn’t seen this board yet. Scan it to confirm it matches the circuit.</p>
      )}
      {board.matches === false && (
        <>
          <p className="notice warn">
            Rewire the board to match {board.branch}
            {board.assumed_from ? ` (assuming it still matches ${shortSha(board.assumed_from)})` : ''}, then scan to
            confirm:
          </p>
          <RewireGuide steps={board.guide} />
          <div className="board-frame">
            <Breadboard circuit={circuit} highlight={holes} />
          </div>
        </>
      )}
      <p className="muted small">
        Last confirmed at {shortSha(board.state.matches_commit)}
        {board.state.updated_at ? ` · ${new Date(board.state.updated_at).toLocaleString()}` : ''}
      </p>
    </section>
  )
}

// ── Branches ──────────────────────────────────────────────────────────────────────────────────

function BranchesPanel({
  branches,
  act,
}: {
  branches: Branch[] | null
  act: (fn: () => Promise<string | void>) => Promise<void>
}) {
  const [name, setName] = useState('')

  const create = () =>
    act(async () => {
      const b = await createBranch(name.trim())
      setName('')
      return `Created ${b.name} at ${shortSha(b.head)}. Switch to it to start changing the circuit.`
    })

  const switchTo = (branch: string) =>
    act(async () => {
      const report = await checkout(branch)
      return report.matches === false
        ? `Switched to ${branch}. The board needs rewiring; see the steps above.`
        : `Switched to ${branch}.`
    })

  return (
    <section className="panel">
      <h2>Branches</h2>
      {branches === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <table className="table">
          <tbody>
            {branches.map((b) => (
              <tr key={b.name} className={b.current ? 'current' : undefined}>
                <td>
                  {b.current ? '● ' : ''}
                  {b.name}
                </td>
                <td className="muted">
                  <code>{shortSha(b.head)}</code> {b.subject}
                </td>
                <td>
                  {!b.current && (
                    <button type="button" className="btn" onClick={() => switchTo(b.name)}>
                      Switch
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) create()
        }}
      >
        <label className="field">
          <span>New branch (from the current commit)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. pot-gpio32" />
        </label>
        <button type="submit" className="btn" disabled={!name.trim()}>
          Create branch
        </button>
      </form>
    </section>
  )
}

// ── PR list and creation ──────────────────────────────────────────────────────────────────────

function PRListPanel({
  prs,
  current,
  selectedId,
  onSelect,
  act,
}: {
  prs: PR[] | null
  current: string | undefined
  selectedId: number | null
  onSelect: (id: number) => void
  act: (fn: () => Promise<string | void>) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')

  const open = () =>
    act(async () => {
      const detail = await createPR({ title: title.trim(), description })
      setTitle('')
      setDescription('')
      onSelect(detail.pr.id)
      return `Opened PR #${detail.pr.id}: ${detail.pr.from_branch} → ${detail.pr.into_branch}.`
    })

  return (
    <section className="panel">
      <h2>Pull requests</h2>
      {prs === null ? (
        <p className="muted">Loading…</p>
      ) : prs.length === 0 ? (
        <p className="muted">No PRs yet.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Title</th>
              <th>Branches</th>
              <th>Status</th>
              <th>Checks</th>
            </tr>
          </thead>
          <tbody>
            {prs.map((p) => (
              <tr
                key={p.id}
                className={`selectable${selectedId === p.id ? ' selected' : ''}`}
                onClick={() => onSelect(p.id)}
              >
                <td>{p.id}</td>
                <td>{p.title}</td>
                <td className="muted">
                  {p.from_branch} → {p.into_branch}
                </td>
                <td>{p.status}</td>
                <td>{p.checks ? <CheckBadge status={p.checks.overall} /> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (title.trim()) open()
        }}
      >
        <label className="field wide">
          <span>New PR from {current ?? 'the current branch'} into main</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" />
        </label>
        <label className="field wide">
          <span>Description (optional)</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div>
          <button type="submit" className="btn primary" disabled={!title.trim()}>
            Open PR
          </button>
        </div>
      </form>
    </section>
  )
}

// ── PR detail ─────────────────────────────────────────────────────────────────────────────────

function PRDetailPanel({ id, act }: { id: number; act: (fn: () => Promise<string | void>) => Promise<void> }) {
  const [detail, setDetail] = useState<PRDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [warningsConfirmed, setWarningsConfirmed] = useState(false)
  const [backGuide, setBackGuide] = useState<RewireStep[] | null>(null)

  const load = useCallback(async () => {
    try {
      setDetail(await getPR(id)) // reruns checks if the branch has new commits
      setLoadError(null)
    } catch (e) {
      setLoadError(message(e))
    }
  }, [id])

  useEffect(() => {
    let alive = true
    getPR(id) // reruns checks if the branch has new commits
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setLoadError(message(e)))
    return () => {
      alive = false
    }
  }, [id])

  if (loadError) return <p className="notice error">{loadError}</p>
  if (!detail) return <p className="muted">Loading PR #{id}… (checks may rerun on the latest commit)</p>

  const { pr, merge } = detail
  const needsConfirm = merge.allowed && merge.warnings.length > 0 && !warningsConfirmed

  const doMerge = () =>
    act(async () => {
      const merged = await mergePR(pr.id)
      await load()
      const n = merged.merge.warnings.length
      return `Merged PR #${pr.id} into ${pr.into_branch}${n ? ` with ${n} warning${n > 1 ? 's' : ''}` : ''}. Now on ${pr.into_branch}.`
    })

  const doTested = () =>
    act(async () => {
      await markTested(pr.id, note)
      setNote('')
      await load()
      return `Marked PR #${pr.id} as tested.`
    })

  const doClose = () =>
    act(async () => {
      const result = await closePR(pr.id)
      setBackGuide(result.guide)
      await load()
      return `Closed PR #${pr.id}.`
    })

  return (
    <section className="panel stack">
      <div className="row spread">
        <div>
          <h2>
            #{pr.id} {pr.title}
          </h2>
          <p className="muted">
            {pr.from_branch} → {pr.into_branch} · {pr.status}
            {pr.description ? ` · ${pr.description}` : ''}
          </p>
        </div>
        <button type="button" className="btn" onClick={load}>
          Refresh
        </button>
      </div>

      <div className="split">
        <div className="board-pair">
          <figure>
            <figcaption>After ({pr.from_branch}); green is new, red was removed</figcaption>
            <div className="board-frame">
              <Breadboard circuit={detail.after} previous={detail.before} />
            </div>
          </figure>
        </div>
        <div>
          <h3>What changes</h3>
          {detail.summary.length === 0 ? (
            <p className="muted">No circuit changes.</p>
          ) : (
            <ul className="change-lines">
              {detail.summary.map((l) => (
                <li key={l} className={/connected|disconnected/.test(l) ? 'electrical' : undefined}>
                  {/connected|disconnected/.test(l) ? '⚡ ' : ''}
                  {l}
                </li>
              ))}
            </ul>
          )}

          <h3>
            Checks {detail.checks && <CheckBadge status={detail.checks.status} />}{' '}
            <span className="muted small">on {shortSha(detail.checks?.commit)}</span>
          </h3>
          {detail.checks?.results
            .filter((r) => r.status === 'fail')
            .map((r, i) => (
              <p key={i} className="notice error">
                {r.check}: {r.message}
              </p>
            ))}
          {merge.warnings.length > 0 && (
            <div className="notice warn">
              <strong>Warnings (they don’t block the merge):</strong>
              <ul className="note-list">
                {merge.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          {merge.not_checked.length > 0 && (
            <div className="muted small">
              Not checked (missing data, not a pass):
              <ul className="note-list">
                {merge.not_checked.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </div>
          )}

          <h3>Tested on the board</h3>
          {pr.tested.done ? (
            <p>
              <span className="badge tested">tested</span> on {shortSha(pr.tested.commit)}
              {pr.tested.note ? `: ${pr.tested.note}` : ''}
            </p>
          ) : (
            <p className="muted">
              Not tested on the latest commit{pr.tested.note ? ` (earlier note: ${pr.tested.note})` : ''}.
            </p>
          )}
          {pr.status === 'open' && (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault()
                doTested()
              }}
            >
              <label className="field">
                <span>Note</span>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. pot reads 0–3.3 V" />
              </label>
              <button type="submit" className="btn">
                Mark as tested
              </button>
            </form>
          )}
        </div>
      </div>

      {pr.status === 'open' && (
        <div className="stack">
          <p className={merge.allowed ? 'notice ok' : 'notice error'}>
            {merge.allowed ? 'Can merge: ' : 'Can’t merge: '}
            {merge.reason}
          </p>
          {merge.allowed && merge.warnings.length > 0 && (
            <label className="row tight">
              <input type="checkbox" checked={warningsConfirmed} onChange={(e) => setWarningsConfirmed(e.target.checked)} />
              I’ve checked the warnings above
            </label>
          )}
          <div className="row tight">
            <button type="button" className="btn primary" onClick={doMerge} disabled={!merge.allowed || needsConfirm}>
              {merge.allowed && merge.warnings.length > 0 ? 'Merge anyway' : 'Merge'}
            </button>
            <button type="button" className="btn" onClick={doClose}>
              Close without merging
            </button>
          </div>
        </div>
      )}

      {backGuide && backGuide.length > 0 && (
        <div>
          <p className="notice warn">
            To put the board back to {pr.into_branch}, switch to it and follow these steps:
          </p>
          <RewireGuide steps={backGuide} />
        </div>
      )}
    </section>
  )
}
