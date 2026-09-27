import { useCallback, useEffect, useState } from 'react'
import { demoScan, getCircuit, getStatus, scan, type Loaded } from '../api'
import { Breadboard } from '../board/Breadboard'
import { ComponentIcon, componentLabel } from '../components/ComponentIcon'
import { StatusLight } from '../components/StatusLight'
import type { Circuit, StatusResponse } from '../types'

/** Requirement 3: the main screen. The virtual board is the main view; there is no live camera feed. */
export function WorkspaceScreen({ onReview }: { onReview: () => void }) {
  const [circuit, setCircuit] = useState<Loaded<Circuit> | null>(null)
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [scanNote, setScanNote] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const [c, s] = await Promise.all([getCircuit(), getStatus().catch(() => null)])
    setCircuit(c)
    setStatus(s)
  }, [])

  useEffect(() => {
    let alive = true
    refresh().then(() => { if (!alive) { setCircuit(null); setStatus(null) } })
    return () => { alive = false }
  }, [refresh])

  const runScan = async (demo: boolean) => {
    setScanning(true)
    setScanError(null)
    setScanNote(null)
    try {
      const result = await (demo ? demoScan() : scan())
      if (result.observations.length > 0) {
        onReview()
        return
      }
      setScanNote('Scan finished: nothing changed on the board since the last scan.')
      await refresh()
    } catch (e) {
      setScanError(e instanceof Error ? e.message : String(e))
    } finally {
      setScanning(false)
    }
  }

  const offline = circuit?.source === 'example'

  return (
    <main className="workspace">
      <div className="toolbar">
        <button type="button" className="btn primary" onClick={() => runScan(false)} disabled={scanning || offline}>
          {scanning ? 'Scanning…' : 'Scan'}
        </button>
        {status && status.pending > 0 && (
          <button type="button" className="btn" onClick={onReview}>
            Review {status.pending} {status.pending === 1 ? 'change' : 'changes'}
          </button>
        )}
        <span className="spacer" />
        {offline && (
          <span className="chip" title="The API isn't running (benchlog serve), so example data is shown.">
            example data
          </span>
        )}
        <StatusLight />
      </div>

      {scanning && <p className="notice">Taking a photo and probing the ESP32…</p>}
      {scanNote && <p className="notice">{scanNote}</p>}
      {scanError && (
        <div className="notice error" role="alert">
          <p>
            <strong>Scan failed.</strong> {scanError}
          </p>
          <p className="muted small">
            No camera set up yet? Run a demo scan: it pretends the pot signal wire moved from A4 to A12.
          </p>
          <button type="button" className="btn" onClick={() => runScan(true)} disabled={scanning}>
            Run a demo scan
          </button>
        </div>
      )}

      <section className="board-area board-frame" data-scanning={scanning ? '' : undefined}>{circuit && <Breadboard circuit={circuit.data} />}</section>

      <aside className="side-panel">
        <h2>Circuit</h2>
        {circuit === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <dl className="facts">
            <dt>Board</dt>
            <dd>{circuit.data.board.toUpperCase()}</dd>
            <dt>Parts</dt>
            <dd>
              {circuit.data.components.length === 0 ? (
                <span className="muted">none</span>
              ) : (
                <ul className="component-icon-list">
                  {circuit.data.components.map((c) => (
                    <li key={c.id} className="component-icon-item" title={`${c.id} — ${c.value ?? componentLabel(c.type)}`}>
                      <ComponentIcon type={c.type} size={28} />
                      <span className="component-icon-label">{c.id}</span>
                    </li>
                  ))}
                </ul>
              )}
            </dd>
            <dt>Wires</dt>
            <dd>{circuit.data.wires.length}</dd>
            {status && (
              <>
                <dt>Branch</dt>
                <dd>{status.branch}</dd>
                <dt>Last commit</dt>
                <dd>{status.head ? status.head.subject : 'none yet'}</dd>
                <dt>Uncommitted</dt>
                <dd>{status.changes.lines.length ? `${status.changes.lines.length} changes` : 'none'}</dd>
              </>
            )}
          </dl>
        )}
      </aside>
    </main>
  )
}
