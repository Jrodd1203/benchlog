import { useEffect, useState } from 'react'
import { getCircuit, type Loaded } from '../api'
import { Breadboard } from '../board/Breadboard'
import { FutureButton } from '../components/FutureButton'
import { StatusLight } from '../components/StatusLight'
import type { Circuit } from '../types'

/** Requirement 3: the main screen. The virtual board is the main view; there is no live camera feed. */
export function WorkspaceScreen() {
  const [circuit, setCircuit] = useState<Loaded<Circuit> | null>(null)

  useEffect(() => {
    getCircuit().then(setCircuit)
  }, [])

  return (
    <main className="workspace">
      <div className="toolbar">
        <FutureButton primary needs="POST /api/scan with the calibrated camera + serial probe">
          Scan
        </FutureButton>
        <span className="spacer" />
        {circuit?.source === 'example' && (
          <span className="chip" title="The API isn't running (benchlog serve), so example data is shown.">
            example data
          </span>
        )}
        <StatusLight />
      </div>

      <section className="board-area board-frame">{circuit && <Breadboard circuit={circuit.data} />}</section>

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
      </aside>
    </main>
  )
}
