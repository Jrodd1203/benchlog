import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  acceptObservations,
  editObservation,
  getCircuit,
  getLastScan,
  getPendingObservations,
  rejectObservations,
} from '../api'
import { Breadboard } from '../board/Breadboard'
import { applyObservations, describeObservation } from '../board/observations'
import { FutureButton } from '../components/FutureButton'
import type { Circuit, Hole, Observation, ProposalVerdict } from '../types'

const KIND_LABEL = { added: 'Added', removed: 'Removed', moved: 'Moved' } as const

/** How the Notion requirement names Tomas's verdicts. */
function verdictLabel(v: ProposalVerdict | undefined): { text: string; tone: 'ok' | 'bad' | 'neutral' } {
  if (v?.verdict === 'confirmed') return { text: 'Confirmed by ESP32', tone: 'ok' }
  if (v?.verdict === 'conflict') return { text: 'Conflict', tone: 'bad' }
  return { text: 'Camera only', tone: 'neutral' }
}

/** Requirement 4: review what a scan detected, then accept, reject, or correct each change. */
export function ReviewScreen({ onCommit, onWorkspace }: { onCommit: () => void; onWorkspace: () => void }) {
  const [circuit, setCircuit] = useState<Circuit | null>(null)
  const [pending, setPending] = useState<Observation[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [picking, setPicking] = useState<{ id: string; end: 'a' | 'b' } | null>(null)
  const [done, setDone] = useState<string[] | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, obs] = await Promise.all([getCircuit(), getPendingObservations()])
      setCircuit(c.data)
      setPending(obs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPending([])
    }
  }, [])

  useEffect(() => {
    let alive = true
    Promise.all([getCircuit(), getPendingObservations()])
      .then(([c, obs]) => {
        if (!alive) return
        setCircuit(c.data)
        setPending(obs)
      })
      .catch((e) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setPending([])
      })
    return () => {
      alive = false
    }
  }, [])

  const lastScan = getLastScan()
  const verdicts = useMemo(
    () => new Map((lastScan?.reconciliation.proposals ?? []).map((p) => [p.observation_id, p])),
    [lastScan],
  )
  const warnings = [...(lastScan?.warnings ?? []), ...(lastScan?.reconciliation.warnings ?? [])]
  const proposed = useMemo(
    () => (circuit && pending ? applyObservations(circuit, pending) : null),
    [circuit, pending],
  )
  const uncertain = pending?.flatMap((o) => o.uncertain_holes) ?? []

  const act = async (fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true)
    try {
      await fn()
      after?.()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const accept = (ids?: string[]) =>
    act(async () => {
      const res = await acceptObservations(ids)
      setDone(res.changes.lines)
    })

  const pickHole = (hole: Hole) => {
    if (!picking) return
    const { id, end } = picking
    setPicking(null)
    act(() => editObservation(id, { [end]: hole }))
  }

  if (pending === null) return <p className="page muted">Loading changes…</p>

  return (
    <div className="review">
      <header className="review-head">
        <div>
          <h1>Review changes</h1>
          <p className="muted">
            {pending.length === 0
              ? 'Nothing waiting for review.'
              : `${pending.length} ${pending.length === 1 ? 'change' : 'changes'} detected by the last scan. Accept the ones that are right.`}
          </p>
        </div>
        {pending.length > 1 && (
          <div className="row tight">
            <button type="button" className="btn" disabled={busy} onClick={() => act(() => rejectObservations())}>
              Reject all
            </button>
            <button type="button" className="btn primary" disabled={busy} onClick={() => accept()}>
              Accept all
            </button>
          </div>
        )}
      </header>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {warnings.map((w) => (
        <p key={w} className="notice warn">
          {w}
        </p>
      ))}
      {done && (
        <div className="notice ok">
          <p>
            <strong>Accepted.</strong> The circuit now includes {done.length ? done.join('; ') : 'these changes'}.
          </p>
          <button type="button" className="btn primary" onClick={onCommit}>
            Commit these changes
          </button>
        </div>
      )}
      {picking && (
        <p className="notice pick">
          Click the hole where wire end <strong>{picking.end.toUpperCase()}</strong> really is.{' '}
          <button type="button" className="link-btn" onClick={() => setPicking(null)}>
            Cancel
          </button>
        </p>
      )}

      <div className="review-body">
        <section className="board-frame">
          {proposed && circuit && (
            <Breadboard
              circuit={proposed}
              previous={circuit}
              highlight={uncertain}
              onHoleClick={picking ? pickHole : undefined}
            />
          )}
          <p className="legend">
            <span className="key added">added</span>
            <span className="key removed">removed</span>
            <span className="key uncertain">camera unsure</span>
          </p>
        </section>

        <ol className="change-list">
          {pending.length === 0 && (
            <li className="empty">
              <p>Scan the board to see what changed since the last commit.</p>
              <button type="button" className="btn" onClick={onWorkspace}>
                Back to workspace
              </button>
            </li>
          )}
          {pending.map((o) => {
            const v = verdictLabel(verdicts.get(o.id))
            const message = verdicts.get(o.id)?.message
            return (
              <li key={o.id} className={`change ${o.kind}`}>
                <div className="change-top">
                  <span className={`kind ${o.kind}`}>{KIND_LABEL[o.kind]}</span>
                  <span className={`verdict ${v.tone}`}>{v.text}</span>
                </div>
                <p className="change-text">{circuit ? describeObservation(o, circuit) : o.object_id}</p>
                {message && <p className="change-msg">{message}</p>}
                {o.uncertain_holes.length > 0 && (
                  <p className="change-msg">Camera unsure about {o.uncertain_holes.join(', ')}</p>
                )}
                <p className="muted small">Confidence {Math.round(o.confidence * 100)}%</p>
                <div className="row tight">
                  <button type="button" className="btn primary" disabled={busy} onClick={() => accept([o.id])}>
                    Accept
                  </button>
                  <button type="button" className="btn" disabled={busy} onClick={() => act(() => rejectObservations([o.id]))}>
                    Reject
                  </button>
                  {o.object_type === 'wire' && o.kind !== 'removed' && (
                    <>
                      <button type="button" className="btn" disabled={busy} onClick={() => setPicking({ id: o.id, end: 'a' })}>
                        Fix end A
                      </button>
                      <button type="button" className="btn" disabled={busy} onClick={() => setPicking({ id: o.id, end: 'b' })}>
                        Fix end B
                      </button>
                    </>
                  )}
                  <FutureButton needs="GET /api/observations/{id}/photo (camera crop)">View photo</FutureButton>
                </div>
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}
