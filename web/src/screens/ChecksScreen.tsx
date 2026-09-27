import { useEffect, useState } from 'react'
import { getCircuit } from '../api'
import { getSavedChecks, holesFor, runChecks, shortSha, type CheckReport } from '../api/prs'
import { Breadboard } from '../board/Breadboard'
import { CheckBadge } from '../components/CheckBadge'
import type { Circuit } from '../types'
import './prs.css'

/** Requirement 9: run the circuit checks and see what they flag on the board. */
export function ChecksScreen() {
  const [circuit, setCircuit] = useState<Circuit | null>(null)
  const [report, setReport] = useState<CheckReport | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getCircuit().then((c) => alive && setCircuit(c.data))
    // Show the last saved report for HEAD right away, if there is one.
    getSavedChecks('HEAD')
      .then((r) => alive && r && setReport(r))
      .catch(() => {}) // no commits yet, or the server is down: the Run button says why
    return () => {
      alive = false
    }
  }, [])

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      setReport(await runChecks())
      setSelected(null)
      setCircuit((await getCircuit()).data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  const results = report?.results ?? []
  const count = (status: string) => results.filter((r) => r.status === status).length
  const highlight = circuit && selected !== null ? holesFor(circuit, results[selected].ids) : []

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1>Checks</h1>
          <p className="muted">
            Shorts, unconfirmed wires, ESP32 pin rules, and what the serial agent sensed. Probes the ESP32 first if it’s
            connected.
          </p>
        </div>
        <button type="button" className="btn primary" onClick={run} disabled={running}>
          {running ? 'Running…' : 'Run checks'}
        </button>
      </header>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}

      {report && (
        <section className="panel stack">
          <div className="row">
            <CheckBadge status={report.status} />
            <span>
              on {report.commit ? `commit ${shortSha(report.commit)}` : 'uncommitted changes'} ·{' '}
              <span className="muted">{new Date(report.ran_at).toLocaleString()}</span>
            </span>
            <span className="muted small">
              {count('fail')} failing · {count('needs_confirmation')} to confirm · {count('not_supported')} not checked
            </span>
          </div>

          <table className="table check-results">
            <thead>
              <tr>
                <th>Status</th>
                <th>Check</th>
                <th>Message</th>
                <th>Involves</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr
                  key={`${r.check}-${i}`}
                  className={`${r.ids.length ? 'selectable' : ''}${selected === i ? ' selected' : ''}`}
                  onClick={() => r.ids.length && setSelected(selected === i ? null : i)}
                >
                  <td>
                    <CheckBadge status={r.status} />
                  </td>
                  <td>{r.check}</td>
                  <td>{r.message}</td>
                  <td className="ids">{r.ids.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Click a row to highlight what it involves on the board.</p>
        </section>
      )}

      {!report && !error && <p className="muted">No saved report for the latest commit yet. Run the checks.</p>}

      {circuit && (
        <div className="board-frame">
          <Breadboard circuit={circuit} highlight={highlight} />
        </div>
      )}
    </main>
  )
}
