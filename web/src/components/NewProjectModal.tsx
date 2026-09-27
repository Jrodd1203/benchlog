import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../api'

/** Only the BB830 template exists so far (see SetupScreen). */
const BOARDS = [{ value: 'bb830', label: 'BB830 (830 tie points, 63 rows)' }]

export function NewProjectModal({
  onCreate,
  onClose,
}: {
  onCreate: (input: { name: string; board: string }) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [board, setBoard] = useState(BOARDS[0].value)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Close on Escape, but not while a submit is in flight (avoid orphaning the request).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [submitting, onClose])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setSubmitting(true)
    setError(null)
    try {
      await onCreate({ name: trimmed, board })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the project.')
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={() => !submitting && onClose()}>
      <div className="modal panel" role="dialog" aria-modal="true" aria-labelledby="new-project-title" onClick={(e) => e.stopPropagation()}>
        <form onSubmit={handleSubmit}>
          <h2 id="new-project-title">New project</h2>
          <label className="field">
            <span>Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ESP32 pot demo"
              autoFocus
              required
            />
          </label>
          <label className="field">
            <span>Board</span>
            <select value={board} onChange={(e) => setBoard(e.target.value)}>
              {BOARDS.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
          {error && <p className="note bad">{error}</p>}
          <div className="row spread">
            <button type="button" className="btn" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={submitting || !name.trim()}>
              {submitting ? 'Creating…' : 'Create project'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
