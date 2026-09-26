import { useState } from 'react'
import { FutureButton } from '../components/FutureButton'

const STEPS = [
  { id: 'board', title: 'Breadboard type' },
  { id: 'esp32', title: 'Place the ESP32' },
  { id: 'camera', title: 'Calibrate camera' },
  { id: 'parts', title: 'Declare parts' },
  { id: 'baseline', title: 'Baseline photo' },
] as const

type StepId = (typeof STEPS)[number]['id']

/** Requirement 2: one-time setup per project. */
export function SetupScreen({ onDone }: { onDone: () => void }) {
  const [index, setIndex] = useState(0)
  const step = STEPS[index]
  const last = index === STEPS.length - 1

  return (
    <main className="page narrow">
      <header className="page-head">
        <div>
          <h1>Project setup</h1>
          <p className="muted">One time per project. You can come back to any step later.</p>
        </div>
      </header>

      <ol className="stepper">
        {STEPS.map((s, i) => (
          <li key={s.id} className={i === index ? 'current' : i < index ? 'done' : ''}>
            <button type="button" onClick={() => setIndex(i)}>
              <span className="step-num">{i + 1}</span>
              {s.title}
            </button>
          </li>
        ))}
      </ol>

      <section className="panel step-body">
        <h2>{step.title}</h2>
        <StepContent id={step.id} />
      </section>

      <div className="row spread">
        <button type="button" className="btn" disabled={index === 0} onClick={() => setIndex(index - 1)}>
          Back
        </button>
        {last ? (
          <button type="button" className="btn primary" onClick={onDone}>
            Go to workspace
          </button>
        ) : (
          <button type="button" className="btn primary" onClick={() => setIndex(index + 1)}>
            Next
          </button>
        )}
      </div>
    </main>
  )
}

function StepContent({ id }: { id: StepId }) {
  switch (id) {
    case 'board':
      return (
        <>
          <p>Which breadboard is this project built on?</p>
          <label className="field">
            <span>Board</span>
            <select defaultValue="bb830">
              <option value="bb830">BB830 (830 tie points, 63 rows)</option>
            </select>
          </label>
          <p className="note">Only the BB830 template exists so far. Saving the choice needs PUT /api/circuit.</p>
        </>
      )
    case 'esp32':
      return (
        <>
          <p>Click the hole where the ESP32's top-left pin (EN) sits. Its other 29 pins are filled in automatically.</p>
          <div className="placeholder-board">Virtual breadboard goes here (next task)</div>
          <div className="row">
            <FutureButton needs="the virtual breadboard (task 2) + PUT /api/circuit">Save ESP32 position</FutureButton>
          </div>
        </>
      )
    case 'camera':
      return (
        <>
          <p>
            Point the camera at the empty board. Mark the four board corners, then check that the grey hole
            overlay lines up with the real holes. The live feed is only shown on this step.
          </p>
          <div className="placeholder-board camera">Live camera feed + hole overlay</div>
          <div className="row">
            <FutureButton needs="camera access in the browser">Start camera</FutureButton>
            <FutureButton needs="corner marking on the feed">Mark corners</FutureButton>
            <FutureButton needs="a calibration endpoint (Person 4, vision/calibration.py)">Save calibration</FutureButton>
          </div>
        </>
      )
    case 'parts':
      return (
        <>
          <p>List the parts you'll use so scans can name what they see.</p>
          <table className="table">
            <thead>
              <tr>
                <th>Part</th>
                <th>Value</th>
                <th>ID</th>
              </tr>
            </thead>
            <tbody>
              <tr className="muted">
                <td colSpan={3}>No parts declared yet.</td>
              </tr>
            </tbody>
          </table>
          <div className="row">
            <FutureButton needs="a part form + PUT /api/circuit">Add part</FutureButton>
          </div>
        </>
      )
    case 'baseline':
      return (
        <>
          <p>With the board empty (except the ESP32), take the baseline photo. Every later scan is compared to it.</p>
          <div className="placeholder-board camera">Baseline photo preview</div>
          <div className="row">
            <FutureButton primary needs="a baseline endpoint (Person 4, vision/capture.py)">
              Take baseline photo
            </FutureButton>
          </div>
        </>
      )
  }
}
