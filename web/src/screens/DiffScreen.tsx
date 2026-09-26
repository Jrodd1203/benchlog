import { useEffect, useState } from 'react'
import { getCircuit, getCircuitAt, getDiff, getTimeline } from '../api'
import { Breadboard } from '../board/Breadboard'
import type { Circuit, TimelineEntry } from '../types'

/** "" means the working (uncommitted) circuit. */
const WORKING = ''

/** Requirement 8: compare any two versions. */
export function DiffScreen() {
  const [entries, setEntries] = useState<TimelineEntry[] | null>(null)
  const [live, setLive] = useState(true)
  const [oldRev, setOldRev] = useState<string>('')
  const [newRev, setNewRev] = useState<string>(WORKING)
  const [boards, setBoards] = useState<{ before: Circuit; after: Circuit } | null>(null)
  const [lines, setLines] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    getTimeline().then(({ data, source }) => {
      if (!alive) return
      setEntries(data)
      setLive(source === 'api')
      // Default: last commit vs. working circuit (what `benchlog diff` shows).
      setOldRev(data[data.length - 1]?.sha ?? '')
      setNewRev(source === 'api' ? WORKING : (data[data.length - 1]?.sha ?? ''))
      if (source !== 'api' && data.length > 1) setOldRev(data[data.length - 2].sha)
    })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!oldRev) return
    let alive = true
    const load = (rev: string) => (rev === WORKING ? getCircuit().then((c) => c.data) : getCircuitAt(rev))
    Promise.all([load(oldRev), load(newRev)]).then(([before, after]) => alive && setBoards({ before, after }))
    if (live) {
      getDiff(oldRev, newRev || undefined)
        .then((d) => {
          if (!alive) return
          setLines(d.lines)
          setError(null)
        })
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
    }
    return () => {
      alive = false
    }
  }, [oldRev, newRev, live])

  const revisionOptions = [...(entries ?? [])].reverse().map((e) => (
    <option key={e.sha} value={e.sha}>
      {e.shortSha} {e.message}
    </option>
  ))

  // "From" can't be the working circuit: getDiff() has no way to ask for a diff *starting*
  // at the uncommitted working tree (omitting `old` means HEAD, not working), so that option
  // is only offered for "To".
  const oldOptions = <>{revisionOptions}</>
  const newOptions = (
    <>
      {live && <option value={WORKING}>Working circuit (uncommitted)</option>}
      {revisionOptions}
    </>
  )

  return (
    <div className="timeline">
      <header className="page-head">
        <div>
          <h1>Compare versions</h1>
          <p className="muted">Green holes are new in the second version, red ones were removed.</p>
        </div>
      </header>

      <div className="scrubber diff-pickers">
        <label className="field">
          <span>From</span>
          <select value={oldRev} onChange={(e) => setOldRev(e.target.value)}>
            {oldOptions}
          </select>
        </label>
        <label className="field">
          <span>To</span>
          <select value={newRev} onChange={(e) => setNewRev(e.target.value)}>
            {newOptions}
          </select>
        </label>
        {!live && (
          <span className="chip" title="The API isn't running, so mock history is shown and the summary is unavailable.">
            example data
          </span>
        )}
      </div>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}

      <div className="board-frame">{boards && <Breadboard circuit={boards.after} previous={boards.before} />}</div>

      {live && (
        <section className="commit-info">
          <h2>What changed</h2>
          {lines === null ? (
            <p className="muted">Loading…</p>
          ) : lines.length === 0 ? (
            <p className="muted">These two versions are the same circuit.</p>
          ) : (
            <ul className="change-lines">
              {lines.map((l) => (
                <li key={l} className={/connected|disconnected/.test(l) ? 'electrical' : undefined}>
                  {/connected|disconnected/.test(l) ? '⚡ ' : ''}
                  {l}
                </li>
              ))}
            </ul>
          )}
          <p className="muted small">⚡ marks electrical changes; the rest only moved to a different hole on the same strip.</p>
        </section>
      )}
    </div>
  )
}
