import { useCallback, useEffect, useRef, useState } from 'react'
import { getCircuitAt, getTimeline, markCheckpoint, unmarkCheckpoint, type Source } from '../api'
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
  const [guideMode, setGuideMode] = useState(false)
  const [checkpointBusy, setCheckpointBusy] = useState(false)
  const [checkpointError, setCheckpointError] = useState<string | null>(null)

  const load = useCallback(async (keepSha?: string) => {
    const { data, source } = await getTimeline()
    setEntries(data)
    setSource(source)
    const target = keepSha ? data.findIndex((e) => e.sha === keepSha) : -1
    setIndex(target >= 0 ? target : data.length - 1)
  }, [])

  useEffect(() => {
    let alive = true
    getTimeline().then(({ data, source }) => {
      if (!alive) return
      setEntries(data)
      setSource(source)
      setIndex(data.length - 1)
    })
    return () => { alive = false }
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

  // Live refs so the keyboard effect always sees current guide state without re-registering.
  const goVisibleRef = useRef<(i: number) => void>(() => {})
  const visibleIndexRef = useRef(0)

  // Autoplay loops back to the first commit.
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setIndex((i) => (i >= last ? 0 : i + 1)), speed)
    return () => clearInterval(id)
  }, [playing, speed, last])

  // ← / → step, Space plays/pauses (ignored while typing in a field).
  // goVisibleRef always points to the current goVisible so guide mode is respected without
  // re-registering the listener on every render.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement && e.target.type !== 'range') return
      if (e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowLeft') goVisibleRef.current(visibleIndexRef.current - 1)
      else if (e.key === 'ArrowRight') goVisibleRef.current(visibleIndexRef.current + 1)
      else if (e.key === ' ') {
        e.preventDefault()
        setPlaying((p) => !p)
      } else return
      if (e.target instanceof HTMLInputElement) e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (entries.length === 0) return <p className="muted page">Loading history…</p>
  const entry = entries[index]

  // In guide mode, show only checkpointed commits in order.
  const guideEntries = entries.filter((e) => e.checkpoint)
  const guideIndex = guideEntries.findIndex((e) => e.sha === entry.sha)
  const visibleEntries = guideMode ? guideEntries : entries
  const visibleIndex = guideMode ? Math.max(0, guideIndex) : index
  const visibleLast = visibleEntries.length - 1

  const goVisible = (i: number) => {
    const clamped = Math.max(0, Math.min(visibleLast, i))
    const sha = visibleEntries[clamped]?.sha
    if (sha) {
      const realIdx = entries.findIndex((e) => e.sha === sha)
      if (realIdx >= 0) setIndex(realIdx)
    }
  }
  goVisibleRef.current = goVisible
  visibleIndexRef.current = visibleIndex

  const handleMarkCheckpoint = async (label: string, note: string) => {
    setCheckpointBusy(true)
    setCheckpointError(null)
    try {
      await markCheckpoint(entry.sha, label, note)
      await load(entry.sha)
    } catch (e) {
      setCheckpointError(e instanceof Error ? e.message : String(e))
    } finally {
      setCheckpointBusy(false)
    }
  }

  const handleUnmarkCheckpoint = async () => {
    setCheckpointBusy(true)
    setCheckpointError(null)
    try {
      await unmarkCheckpoint(entry.sha)
      await load(entry.sha)
    } catch (e) {
      setCheckpointError(e instanceof Error ? e.message : String(e))
    } finally {
      setCheckpointBusy(false)
    }
  }

  const stepNumber = guideMode && entry.checkpoint
    ? guideEntries.findIndex((e) => e.sha === entry.sha) + 1
    : null

  return (
    <div className="timeline">
      <div className="timeline-mode-bar">
        <div className="timeline-mode-tabs">
          <button
            type="button"
            className={`btn tab${!guideMode ? ' active' : ''}`}
            onClick={() => setGuideMode(false)}
          >
            Full history
          </button>
          <button
            type="button"
            className={`btn tab${guideMode ? ' active' : ''}`}
            onClick={() => {
              setGuideMode(true)
              if (guideEntries.length > 0) {
                const realIdx = entries.findIndex((e) => e.sha === guideEntries[0].sha)
                if (realIdx >= 0) setIndex(realIdx)
              }
            }}
            title={guideEntries.length === 0 ? 'No checkpoints yet — mark commits as guide steps first' : undefined}
          >
            Build guide
            {guideEntries.length > 0 && (
              <span className="tab-count">{guideEntries.length}</span>
            )}
          </button>
        </div>
        {guideMode && guideEntries.length === 0 && (
          <p className="muted small">No checkpoints yet. Switch to Full history and mark commits as guide steps.</p>
        )}
      </div>

      {entry.electrical && (
        <div className="hazard" role="status">
          ⚡ Electrical change: this commit made or broke a connection, not just moved a wire within its strip.
        </div>
      )}

      <div className="board-frame" style={{ position: 'relative' }}>
        {boards && <Breadboard circuit={boards.circuit} previous={boards.previous} />}
        {entry.check === 'fail' && (
          <div className="board-stamp fail" aria-hidden="true">CHECKS FAIL</div>
        )}
        {guideMode && stepNumber !== null && (
          <div className="board-stamp guide" aria-hidden="true">Step {stepNumber}</div>
        )}
      </div>

      <section className={`commit-info${entry.electrical ? ' electrical' : ''}${entry.check === 'fail' ? ' fail' : ''}${entry.checkpoint ? ' checkpoint' : ''}`}>
        <div className="commit-head">
          <div>
            {entry.checkpoint && (
              <p className="guide-label">
                {stepNumber !== null ? `Step ${stepNumber}: ` : ''}
                {entry.checkpoint.label}
              </p>
            )}
            <h2>{entry.message}</h2>
          </div>
          <div className="commit-badges">
            {entry.check && entry.check !== 'none' && (
              <span className={`badge ${entry.check}`}>{entry.check === 'pass' ? 'Checks pass' : 'Checks fail'}</span>
            )}
            {entry.tested && <span className="badge tested">Tested on the bench</span>}
            {entry.checkpoint && <span className="badge guide">Guide step</span>}
          </div>
        </div>
        <p className="commit-meta">
          <code>{entry.shortSha}</code>
          {entry.author}, {new Date(entry.date).toLocaleString()}
        </p>
        {(entry.checkpoint?.note ?? entry.note) && (
          <p className="commit-note">{entry.checkpoint?.note ?? entry.note}</p>
        )}
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

        {checkpointError && (
          <p className="notice error small" role="alert">{checkpointError}</p>
        )}
        {source === 'api' && (
          entry.checkpoint
            ? (
              <button
                type="button"
                className="btn"
                disabled={checkpointBusy}
                onClick={handleUnmarkCheckpoint}
              >
                Remove from guide
              </button>
            )
            : (
              <CheckpointForm key={entry.sha} onSave={handleMarkCheckpoint} busy={checkpointBusy} />
            )
        )}
      </section>

      <div className="scrubber">
        <div className="scrub-controls">
          <button type="button" className="btn icon" onClick={() => goVisible(visibleIndex - 1)} disabled={visibleIndex === 0} aria-label="Previous commit">
            ◀
          </button>
          <button type="button" className="btn icon" onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? '⏸' : '▶'}
          </button>
          <button type="button" className="btn icon" onClick={() => goVisible(visibleIndex + 1)} disabled={visibleIndex === visibleLast} aria-label="Next commit">
            ⏭
          </button>
          {!guideMode && (
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
              {SPEEDS.map((s) => (
                <option key={s.ms} value={s.ms}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="scrub-track">
          <input
            type="range"
            min={0}
            max={visibleLast}
            step={1}
            value={visibleIndex}
            onChange={(e) => goVisible(Number(e.target.value))}
            aria-label="Commit"
            aria-valuetext={`${visibleIndex + 1} of ${visibleEntries.length}: ${visibleEntries[visibleIndex]?.message ?? ''}`}
          />
          <div className="pips" aria-hidden="true">
            {visibleEntries.map((e, i) => (
              <span
                key={e.sha}
                className={[
                  'pip',
                  i === visibleIndex ? 'active' : '',
                  e.electrical ? 'electrical' : '',
                  e.check === 'fail' ? 'fail' : '',
                  e.checkpoint ? 'guide' : '',
                ].filter(Boolean).join(' ')}
                style={{ left: `${visibleLast === 0 ? 0 : (i / visibleLast) * 100}%` }}
                title={e.checkpoint ? `Step: ${e.checkpoint.label}` : e.message}
              />
            ))}
          </div>
        </div>

        <span className="scrub-count">
          {visibleIndex + 1} / {visibleEntries.length}
        </span>
        {source === 'example' && (
          <span className="chip" title="The API isn't running or has no commits, so mock history is shown.">
            example data
          </span>
        )}
        {!guideMode && <span className="muted scrub-hint">Arrow keys step, Space plays</span>}
      </div>
    </div>
  )
}

function CheckpointForm({ onSave, busy }: { onSave: (label: string, note: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('')
  const [note, setNote] = useState('')

  if (!open) {
    return (
      <button type="button" className="btn" onClick={() => setOpen(true)}>
        Add to build guide…
      </button>
    )
  }

  return (
    <form
      className="checkpoint-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (label.trim()) {
          onSave(label.trim(), note.trim())
          setLabel('')
          setNote('')
          setOpen(false)
        }
      }}
    >
      <label className="field">
        <span>Step label</span>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. ESP32 + LED confirmed working"
          autoFocus
        />
      </label>
      <label className="field">
        <span>Note (optional)</span>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. pot reads 0–3.3 V, LED blinks at 1 Hz"
        />
      </label>
      <div className="row tight">
        <button type="submit" className="btn primary" disabled={busy || !label.trim()}>
          {busy ? 'Saving…' : 'Save step'}
        </button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  )
}
