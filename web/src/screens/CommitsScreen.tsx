import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { commit, getStatus } from '../api'
import { FutureButton } from '../components/FutureButton'
import type { Commit, StatusResponse } from '../types'

/** Requirement 6: see what changed since the last commit, write a message, commit. */
export function CommitsScreen({ onReview, onTimeline }: { onReview: () => void; onTimeline: () => void }) {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [made, setMade] = useState<Commit | null>(null)

  const load = useCallback(async () => {
    try {
      setStatus(await getStatus())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    let alive = true
    getStatus()
      .then((s) => alive && setStatus(s))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [])

  const changes = status?.changes.lines ?? []
  const canCommit = !busy && message.trim().length > 0 && changes.length > 0

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!canCommit) return
    setBusy(true)
    try {
      const res = await commit(message.trim())
      setMade(res.commit)
      setMessage('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page narrow">
      <header className="page-head">
        <div>
          <h1>Changes and commits</h1>
          {status && (
            <p className="muted">
              On branch <code>{status.branch}</code>
              {status.head ? (
                <>
                  , last commit <code>{status.head.short_sha}</code> {status.head.subject}
                </>
              ) : (
                ', no commits yet'
              )}
            </p>
          )}
        </div>
      </header>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {made && (
        <div className="notice ok">
          <p>
            <strong>Committed</strong> <code>{made.short_sha}</code> {made.subject}
          </p>
          <button type="button" className="btn" onClick={onTimeline}>
            See it on the timeline
          </button>
        </div>
      )}
      {status && status.pending > 0 && (
        <div className="notice warn">
          <p>
            {status.pending} scanned {status.pending === 1 ? 'change is' : 'changes are'} still waiting for review and
            won’t be in this commit.
          </p>
          <button type="button" className="btn" onClick={onReview}>
            Review first
          </button>
        </div>
      )}

      <section className="panel">
        <h2>Uncommitted changes</h2>
        {status === null && !error ? (
          <p className="muted">Loading…</p>
        ) : changes.length === 0 ? (
          <p className="muted">The circuit matches the last commit. Scan and accept changes to have something to commit.</p>
        ) : (
          <ul className="change-lines">
            {changes.map((l) => (
              <li key={l} className={/connected|disconnected/.test(l) ? 'electrical' : undefined}>
                {l}
              </li>
            ))}
          </ul>
        )}

        <form className="commit-form" onSubmit={submit}>
          <label className="field wide">
            <span>Commit message</span>
            <input
              type="text"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="e.g. Move pot signal to GPIO34"
              disabled={changes.length === 0}
            />
          </label>
          <div className="row tight">
            <button type="submit" className="btn primary" disabled={!canCommit}>
              {busy ? 'Committing…' : 'Commit circuit'}
            </button>
            <FutureButton needs="POST /api/stage and /api/unstage (stage circuit and firmware separately)">
              Stage firmware
            </FutureButton>
            <FutureButton needs="POST /api/commits/{sha}/tested">Mark as tested</FutureButton>
          </div>
        </form>
      </section>
    </main>
  )
}
