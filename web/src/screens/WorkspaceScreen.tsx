import { useEffect, useRef, useState } from 'react'
import {
  acceptObservations,
  getCircuit,
  getStatus,
  postCommit,
  postScan,
  rejectObservations,
  type Loaded,
} from '../api'
import { Breadboard } from '../board/Breadboard'
import { StatusLight } from '../components/StatusLight'
import type { Circuit, ProposalVerdict, Reconciliation, ScanResponse, StatusResponse } from '../types'

type Phase = 'idle' | 'scanning' | 'review' | 'accepting' | 'rejecting' | 'commit' | 'committing' | 'done'

/** Requirement 3: the main screen. The virtual board is the main view; there is no live camera feed. */
export function WorkspaceScreen({ onGoToTimeline }: { onGoToTimeline?: () => void }) {
  const [circuit, setCircuit] = useState<Loaded<Circuit> | null>(null)
  const [previous, setPrevious] = useState<Circuit | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [scan, setScan] = useState<ScanResponse | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [commitMsg, setCommitMsg] = useState('')
  const [commitResult, setCommitResult] = useState<string | null>(null)
  const [status, setStatus] = useState<StatusResponse | null>(null)

  const alive = useRef(true)

  function loadCircuit() {
    getCircuit().then((c) => { if (alive.current) setCircuit(c) })
  }

  function loadStatus() {
    getStatus().then((s) => { if (alive.current) setStatus(s) })
  }

  useEffect(() => {
    alive.current = true
    loadCircuit()
    loadStatus()
    return () => { alive.current = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleScan() {
    setPhase('scanning')
    setScanError(null)
    try {
      const result = await postScan()
      if (!alive.current) return
      // Save the current circuit as "previous" so diffs show up on the board
      setPrevious(circuit?.data ?? null)
      setScan(result)
      // Reload circuit — backend may have updated the working circuit
      const fresh = await getCircuit()
      if (!alive.current) return
      setCircuit(fresh)
      setPhase('review')
    } catch (err) {
      if (!alive.current) return
      setScanError(err instanceof Error ? err.message : 'Scan failed')
      setPhase('idle')
    }
  }

  async function handleAcceptAll() {
    if (!scan) return
    setScanError(null)
    setPhase('accepting')
    try {
      const result = await acceptObservations(null)
      if (!alive.current) return
      setCircuit((prev) => prev ? { ...prev, data: result.circuit } : { data: result.circuit, source: 'api' })
      setPhase('commit')
      if (result.changes.lines.length > 0) setCommitMsg(result.changes.lines.join('; '))
      loadStatus()
    } catch (err) {
      if (!alive.current) return
      setScanError(err instanceof Error ? err.message : 'Accept failed')
      setPhase('review')
    }
  }

  async function handleRejectAll() {
    setScanError(null)
    setPhase('rejecting')
    try {
      await rejectObservations(null)
      if (!alive.current) return
      setPrevious(null)
      setScan(null)
      setPhase('idle')
      loadCircuit()
    } catch (err) {
      if (!alive.current) return
      setScanError(err instanceof Error ? err.message : 'Reject failed')
      setPhase('review')
    }
  }

  async function handleCommit() {
    if (!commitMsg.trim()) return
    setPhase('committing')
    try {
      const result = await postCommit(commitMsg.trim())
      if (!alive.current) return
      const shortSha = result.commit.short_sha
      setCommitResult(shortSha)
      setPhase('done')
      setPrevious(null)
      setScan(null)
      loadStatus()
      setTimeout(() => {
        if (!alive.current) return
        setPhase('idle')
        setCommitResult(null)
        setCommitMsg('')
        loadCircuit()
        loadStatus()
      }, 2000)
    } catch (err) {
      if (!alive.current) return
      setScanError(err instanceof Error ? err.message : 'Commit failed')
      setPhase('commit')
    }
  }

  function handleCancelCommit() {
    setPrevious(null)
    setScan(null)
    setPhase('idle')
  }

  // Build a lookup from observation_id → ProposalVerdict
  const verdictMap = new Map<string, ProposalVerdict>()
  if (scan?.reconciliation) {
    for (const p of scan.reconciliation.proposals) {
      verdictMap.set(p.observation_id, p)
    }
  }

  // Find the hole that actually changed for an observation (not just the first value).
  function getChangedHole(obs: typeof observations[number]): string | null {
    if (obs.kind === 'moved' && obs.before && obs.after) {
      for (const [k, v] of Object.entries(obs.after)) {
        if (obs.before[k] !== v) return v
      }
    }
    if (obs.kind === 'added' && obs.after) return Object.values(obs.after)[0] ?? null
    if (obs.kind === 'removed' && obs.before) return Object.values(obs.before)[0] ?? null
    return null
  }

  const reconciliation: Reconciliation | null = scan?.reconciliation ?? null
  const observations = scan?.observations ?? []
  const conflictProposals = reconciliation?.proposals.filter(p => p.verdict === 'conflict') ?? []

  const showingReview = phase === 'review' || phase === 'accepting' || phase === 'rejecting' || phase === 'commit' || phase === 'committing' || phase === 'done'

  return (
    <main className="workspace">
      <div className="toolbar">
        <button
          className="btn primary"
          disabled={phase !== 'idle'}
          onClick={handleScan}
        >
          {phase === 'scanning' ? 'Scanning…' : 'Scan'}
        </button>
        <span className="spacer" />
        {circuit?.source === 'example' && (
          <span className="chip" title="The API isn't running (benchlog serve), so example data is shown.">
            example data
          </span>
        )}
        {scanError && (
          <span className="chip warn" title={scanError}>
            {scanError}
          </span>
        )}
        <StatusLight />
      </div>

      {conflictProposals.length > 0 && phase === 'review' && (
        <div className="hazard" role="alert">
          ⚡ {conflictProposals[0].message ?? 'Conflict detected — check reconciliation details below.'}
        </div>
      )}

      <section className="board-area board-frame" data-scanning={phase === 'scanning' ? 'true' : undefined}>
        {circuit && (
          <Breadboard
            circuit={circuit.data}
            previous={showingReview && previous ? previous : undefined}
          />
        )}
      </section>

      {/* Review panel */}
      {(phase === 'review' || phase === 'accepting' || phase === 'rejecting' || phase === 'commit' || phase === 'committing') && scan && (
        <div style={{ gridColumn: '1' }}>
          <div className="scan-results">
            <div className="scan-results-header">
              <span>Scan results — {observations.length} observation{observations.length !== 1 ? 's' : ''}</span>
            </div>

            {scan.warnings.length > 0 && (
              <div className="scan-warnings">
                {scan.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
              </div>
            )}
            {reconciliation && reconciliation.warnings.length > 0 && (
              <div className="scan-warnings">
                {reconciliation.warnings.map((w, i) => <div key={i}>⎇ {w}</div>)}
              </div>
            )}

            {observations.length === 0 ? (
              <div style={{ padding: '0.75rem 1rem', color: 'var(--muted)', fontSize: '0.9rem' }}>
                No changes detected.
              </div>
            ) : (
              <ul className="obs-list">
                {observations.map((obs) => {
                  const verdict = verdictMap.get(obs.id)
                  const changedHole = getChangedHole(obs)
                  const isConflict = verdict?.verdict === 'conflict'
                  return (
                    <li key={obs.id} className={`obs-item${isConflict ? ' has-conflict' : ''}`}>
                      <span className={`obs-kind ${obs.kind}`}>{obs.kind}</span>
                      <span className="obs-id">{obs.object_id}</span>
                      {changedHole && <span className="obs-hole">→ {changedHole}</span>}
                      {verdict && verdict.gpios.length > 0 && (
                        <span className="obs-hole">{verdict.gpios.map(g => `GPIO${g}`).join(', ')}</span>
                      )}
                      {obs.confidence < 0.75 && (
                        <span className="verdict-chip not_checked" title={`Camera confidence: ${Math.round(obs.confidence * 100)}%`}>
                          {Math.round(obs.confidence * 100)}% conf
                        </span>
                      )}
                      {verdict && (
                        <span className={`verdict-chip ${verdict.verdict}`}>
                          {verdict.verdict.replace(/_/g, ' ')}
                        </span>
                      )}
                      {isConflict && verdict.message && (
                        <span className="obs-conflict-msg">{verdict.message}</span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}

            <div className="scan-actions">
              {observations.length === 0 ? (
                <button className="btn" onClick={handleCancelCommit}>Discard</button>
              ) : (
                <>
                  <button
                    className="btn primary"
                    disabled={phase !== 'review'}
                    onClick={handleAcceptAll}
                  >
                    {phase === 'accepting' ? 'Accepting…' : 'Accept All'}
                  </button>
                  <button
                    className="btn"
                    disabled={phase !== 'review'}
                    onClick={handleRejectAll}
                  >
                    {phase === 'rejecting' ? 'Rejecting…' : 'Reject All'}
                  </button>
                </>
              )}
            </div>
          </div>

          {(phase === 'commit' || phase === 'committing') && (
            <div className="commit-panel">
              <label htmlFor="commit-msg">Commit message</label>
              <input
                id="commit-msg"
                type="text"
                placeholder="Describe what changed…"
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                disabled={phase === 'committing'}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleCommit() }}
              />
              <div className="commit-panel-actions">
                <button
                  className="btn primary"
                  disabled={!commitMsg.trim() || phase === 'committing'}
                  onClick={handleCommit}
                >
                  {phase === 'committing' ? 'Committing…' : 'Commit'}
                </button>
                <button
                  type="button"
                  className="btn link"
                  onClick={handleCancelCommit}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {phase === 'done' && commitResult && (
        <div style={{ gridColumn: '1' }}>
          <div className="commit-panel success">
            <span className="commit-success">Committed {commitResult}</span>
            {onGoToTimeline && (
              <button type="button" className="btn" onClick={onGoToTimeline} style={{ marginTop: '0.5rem' }}>
                View in Timeline →
              </button>
            )}
          </div>
        </div>
      )}

      <aside className="side-panel">
        <h2>Circuit</h2>
        {circuit === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <dl className="facts">
            <dt>Board</dt>
            <dd>{circuit.data.board.toUpperCase()}</dd>
            <dt>Parts</dt>
            <dd>{circuit.data.components.map((c) => c.id).join(', ') || 'none'}</dd>
            <dt>Wires</dt>
            <dd>{circuit.data.wires.length}</dd>
          </dl>
        )}

        <div className="status-facts">
          <h3>Status</h3>
          {status === null ? (
            <p className="muted" style={{ fontSize: '0.87rem' }}>Loading…</p>
          ) : (
            <dl className="facts">
              <dt>Branch</dt>
              <dd>⎇ {status.branch}</dd>
              <dt>Head</dt>
              <dd>
                {status.head
                  ? <><code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: 'var(--accent)' }}>{status.head.short_sha}</code> {status.head.subject}</>
                  : <span className="muted">no commits yet</span>
                }
              </dd>
              <dt>Pending</dt>
              <dd>
                {status.pending > 0
                  ? <span className="pending-badge">{status.pending}</span>
                  : <span className="muted">none</span>
                }
              </dd>
              <dt>Changes</dt>
              <dd>
                {status.changes.lines.length > 0 ? (
                  <span>
                    {status.changes.lines.slice(0, 2).map((l, i) => (
                      <div key={i} style={{ fontSize: '0.78rem', color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '14rem' }}>{l}</div>
                    ))}
                    {status.changes.lines.length > 2 && (
                      <span style={{ fontSize: '0.78rem', color: 'var(--muted)', opacity: 0.6 }}>+{status.changes.lines.length - 2} more</span>
                    )}
                  </span>
                ) : <span className="muted">No uncommitted changes</span>}
              </dd>
            </dl>
          )}
        </div>
      </aside>
    </main>
  )
}
