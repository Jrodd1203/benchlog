import { useCallback, useEffect, useState } from 'react'
import { getCircuitAt, getTimeline, type Source } from '../api'
import { Breadboard } from '../board/Breadboard'
import type { Circuit, TimelineEntry } from '../types'

const SPEEDS = [
  { label: '0.5×', ms: 2400 },
  { label: '1×', ms: 1200 },
  { label: '2×', ms: 600 },
]

/** Requirement 7: scrub through every commit; the board redraws and highlights what changed. */
export function TimelineScreen() {
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const [source, setSource] = useState<Source>('api')
  const [index, setIndex] = useState(0)
  const [boards, setBoards] = useState<{ circuit: Circuit; previous: Circuit | null } | null>(null)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(SPEEDS[1].ms)

  useEffect(() => {
    getTimeline().then(({ data, source }) => {
      setEntries(data)
      setSource(source)
      setIndex(data.length - 1)
    })
  }, [])

  // Load the circuit at the selected commit and the one before it.
  useEffect(() => {
    if (entries.length === 0) return
    let alive = true
    const prevSha = index > 0 ? entries[index - 1].sha : null
    Promise.all([getCircuitAt(entries[index].sha), prevSha ? getCircuitAt(prevSha) : null]).then(([circuit, previous]) => {
      if (alive) setBoards({ circuit, previous })
    })
    return () => {
      alive = false
    }
  }, [entries, index])

  const last = entries.length - 1
  const go = useCallback((i: number) => setIndex(Math.max(0, Math.min(last, i))), [last])

  // Autoplay loops back to the first commit.
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setIndex((i) => (i >= last ? 0 : i + 1)), speed)
    return () => clearInterval(id)
  }, [playing, speed, last])

  // ← / → step, Space plays/pauses (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement && e.target.type !== 'range') return
      if (e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1))
      else if (e.key === 'ArrowRight') setIndex((i) => Math.min(last, i + 1))
      else if (e.key === ' ') {
        e.preventDefault()
        setPlaying((p) => !p)
      } else return
      if (e.target instanceof HTMLInputElement) e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [last])

  if (entries.length === 0) return <p className="muted page">Loading history…</p>
  const entry = entries[index]

  return (
    <div className="timeline">
      {entry.electrical && (
        <div className="hazard" role="status">
          ⚡ Electrical change: this commit made or broke a connection, not just moved a wire within its strip.
        </div>
      )}

      <div className="board-frame">{boards && <Breadboard circuit={boards.circuit} previous={boards.previous} />}</div>

      <div className="scrubber">
        <div className="scrub-controls">
          <button type="button" className="btn icon" onClick={() => go(index - 1)} disabled={index === 0} aria-label="Previous commit">
            ◀
          </button>
          <button type="button" className="btn icon" onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? '⏸' : '▶'}
          </button>
          <button type="button" className="btn icon" onClick={() => go(index + 1)} disabled={index === last} aria-label="Next commit">
            ⏭
          </button>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
            {SPEEDS.map((s) => (
              <option key={s.ms} value={s.ms}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        <div className="scrub-track">
          <input
            type="range"
            min={0}
            max={last}
            step={1}
            value={index}
            onChange={(e) => go(Number(e.target.value))}
            aria-label="Commit"
            aria-valuetext={`${index + 1} of ${entries.length}: ${entry.message}`}
          />
          <div className="pips" aria-hidden="true">
            {entries.map((e, i) => (
              <span
                key={e.sha}
                className={`pip${i === index ? ' active' : ''}${e.electrical ? ' electrical' : ''}${e.check === 'fail' ? ' fail' : ''}`}
                style={{ left: `${last === 0 ? 0 : (i / last) * 100}%` }}
                title={e.message}
              />
            ))}
          </div>
        </div>

        <span className="scrub-count">
          {index + 1} / {entries.length}
        </span>
        {source === 'example' && (
          <span className="chip" title="The API isn't running or has no commits, so mock history is shown.">
            example data
          </span>
        )}
        <span className="muted small scrub-hint">← → to step · Space to play</span>
      </div>

      <section className={`commit-info${entry.electrical ? ' electrical' : ''}`}>
        <div className="commit-head">
          <h2>{entry.message}</h2>
          <div className="commit-badges">
            {entry.check && entry.check !== 'none' && (
              <span className={`badge ${entry.check}`}>{entry.check === 'pass' ? 'checks pass' : 'checks fail'}</span>
            )}
            {entry.tested && <span className="badge tested">tested</span>}
          </div>
        </div>
        <p className="commit-meta">
          <code>{entry.shortSha}</code> · {entry.author} · {new Date(entry.date).toLocaleString()}
        </p>
        {entry.note && <p className="commit-note">{entry.note}</p>}
        <ul className="change-lines">
          {entry.lines.length === 0 && <li className="muted">No circuit changes.</li>}
          {entry.lines.map((line) => {
            const electricalLine = /connected|different strip/.test(line)
            return (
              <li key={line} className={electricalLine ? 'electrical' : undefined}>
                {electricalLine && '⚡ '}
                {line}
              </li>
            )
          })}
        </ul>
      </section>
    </div>
  )
}
