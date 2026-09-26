import { useCallback, useEffect, useState } from 'react'
import { connectSerial, getLastScan, getSerialPorts, getSerialStatus, probeSerial } from '../api'
import { FutureButton } from '../components/FutureButton'
import { StatusLight } from '../components/StatusLight'
import type { PinState, PortInfo, SerialSnapshot, SerialStatus } from '../types'

const PIN_TEXT: Record<PinState, string> = {
  floating: 'nothing connected',
  pulled_low: 'tied low (GND)',
  pulled_high: 'tied high (3V3)',
  unstable: 'unstable, wire may not be seated',
  unsafe: 'not probed (unsafe pin)',
}

/** Requirement 5: pick the ESP32's port, connect, and see what it senses. */
export function SerialScreen() {
  const [ports, setPorts] = useState<PortInfo[] | null>(null)
  const [port, setPort] = useState('')
  const [status, setStatus] = useState<SerialStatus | null>(null)
  const [snapshot, setSnapshot] = useState<SerialSnapshot | null>(null)
  const [busy, setBusy] = useState<'connect' | 'probe' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshPorts = useCallback(async () => {
    try {
      const list = await getSerialPorts()
      setPorts(list)
      setPort((p) => p || list[0]?.device || '')
      setError(null)
    } catch (e) {
      setPorts([])
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    let alive = true
    getSerialPorts()
      .then((list) => {
        if (!alive) return
        setPorts(list)
        setPort((p) => p || list[0]?.device || '')
      })
      .catch((e) => {
        if (!alive) return
        setPorts([])
        setError(e instanceof Error ? e.message : String(e))
      })
    getSerialStatus().then((s) => {
      if (!alive || !s.status) return
      setStatus(s.status)
      if (s.status.port) setPort(s.status.port)
    })
    return () => {
      alive = false
    }
  }, [])

  const run = async (kind: 'connect' | 'probe') => {
    setBusy(kind)
    setError(null)
    try {
      if (kind === 'connect') setStatus(await connectSerial(port))
      else setSnapshot(await probeSerial())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const warnings = getLastScan()?.reconciliation.warnings ?? []
  const pins = snapshot ? Object.entries(snapshot.probe.pins).sort(([a], [b]) => Number(a) - Number(b)) : []

  return (
    <main className="page narrow">
      <header className="page-head">
        <div>
          <h1>Serial (ESP32)</h1>
          <p className="muted">The ESP32 agent checks the wiring electrically during every scan.</p>
        </div>
        <StatusLight />
      </header>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}

      <section className="panel">
        <h2>Connection</h2>
        <div className="serial-connect">
          <label className="field">
            <span>Port</span>
            <select value={port} onChange={(e) => setPort(e.target.value)} disabled={!ports?.length}>
              {!ports?.length && <option value="">{ports === null ? 'Looking for ports…' : 'No serial ports found'}</option>}
              {ports?.map((p) => (
                <option key={p.device} value={p.device}>
                  {p.device}
                  {p.description ? ` (${p.description})` : ''}
                </option>
              ))}
            </select>
          </label>
          <div className="row tight">
            <button type="button" className="btn" onClick={refreshPorts} disabled={busy !== null}>
              Refresh
            </button>
            <button type="button" className="btn primary" onClick={() => run('connect')} disabled={!port || busy !== null}>
              {busy === 'connect' ? 'Connecting…' : 'Connect'}
            </button>
            <FutureButton needs="POST /api/serial/disconnect">Disconnect</FutureButton>
          </div>
        </div>
        {busy === 'connect' && <p className="muted small">The ESP32 restarts when the port opens; this takes a few seconds.</p>}
        {status && (
          <dl className="facts">
            <dt>Status</dt>
            <dd>{status.connected ? 'connected' : 'not connected'}</dd>
            {status.port && (
              <>
                <dt>Port</dt>
                <dd>{status.port}</dd>
              </>
            )}
            {status.agent && (
              <>
                <dt>Agent</dt>
                <dd>firmware {status.agent}</dd>
              </>
            )}
            {status.pins.length > 0 && (
              <>
                <dt>Probes</dt>
                <dd>GPIO {status.pins.join(', ')}</dd>
              </>
            )}
            {status.last_error && (
              <>
                <dt>Last error</dt>
                <dd>{status.last_error}</dd>
              </>
            )}
          </dl>
        )}
      </section>

      {warnings.length > 0 && (
        <section className="panel">
          <h2>From the last scan</h2>
          {warnings.map((w) => (
            <p key={w} className="notice warn">
              {w}
            </p>
          ))}
        </section>
      )}

      <section className="panel">
        <div className="section-label">
          <h2>What the ESP32 senses</h2>
          <button type="button" className="btn" onClick={() => run('probe')} disabled={!status?.connected || busy !== null}>
            {busy === 'probe' ? 'Probing…' : 'Probe now'}
          </button>
        </div>
        {!snapshot ? (
          <p className="muted">Connect, then probe to read every safe pin and scan the I2C bus.</p>
        ) : (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>GPIO</th>
                  <th>Reading</th>
                </tr>
              </thead>
              <tbody>
                {pins.map(([gpio, state]) => (
                  <tr key={gpio} className={state === 'unstable' ? 'warn-row' : undefined}>
                    <td>
                      <code>GPIO{gpio}</code>
                    </td>
                    <td>{PIN_TEXT[state]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="small">
              I2C (SDA {snapshot.i2c.sda}, SCL {snapshot.i2c.scl}):{' '}
              {snapshot.i2c.devices.length ? snapshot.i2c.devices.join(', ') : 'no devices answered'}
            </p>
          </>
        )}
      </section>
    </main>
  )
}
