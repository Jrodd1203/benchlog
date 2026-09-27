import { useEffect, useState } from 'react'
import { getSerialStatus } from '../api'
import { connectEsp32, disconnectEsp32, useEsp32 } from '../serial/esp32'
import type { SerialState } from '../types'
import './StatusLight.css'

const LABEL: Record<SerialState, string> = {
  connected: 'ESP32 connected',
  disconnected: 'ESP32 not connected',
  unknown: 'ESP32 status unavailable',
}

/**
 * ESP32 status. The browser's own connection (Web Serial) wins; otherwise this polls the server's
 * (local `benchlog serve`). When neither is connected it offers "Connect ESP32" in browsers that
 * support Web Serial (Chrome, Edge).
 */
export function StatusLight() {
  const browser = useEsp32()
  const [state, setState] = useState<SerialState>('unknown')
  const [port, setPort] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const poll = async () => {
      const s = await getSerialStatus()
      if (alive) {
        setState(s.state)
        setPort(s.status?.port ?? null)
      }
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  if (browser.state === 'connected') {
    return (
      <span className="status-light-group">
        <span className="status-light connected" title={browser.label ?? undefined}>
          <span className="dot" aria-hidden="true" />
          ESP32 connected (this browser)
        </span>
        <button type="button" className="btn small" onClick={() => disconnectEsp32()}>
          Disconnect
        </button>
      </span>
    )
  }

  if (state === 'connected') {
    return (
      <span className="status-light connected" title={port ?? undefined}>
        <span className="dot" aria-hidden="true" />
        {LABEL.connected}
      </span>
    )
  }

  return (
    <span className="status-light-group">
      <span className={`status-light ${state}`}>
        <span className="dot" aria-hidden="true" />
        {LABEL[state === 'unknown' ? 'disconnected' : state]}
      </span>
      {browser.state === 'unsupported' ? (
        <span className="muted small">Use Chrome or Edge to connect an ESP32</span>
      ) : (
        <button
          type="button"
          className="btn small"
          onClick={() => connectEsp32()}
          disabled={browser.state === 'connecting'}
        >
          {browser.state === 'connecting' ? 'Connecting…' : 'Connect ESP32'}
        </button>
      )}
      {browser.error && (
        <span className="notice-inline error" role="alert">
          {browser.error}
        </span>
      )}
    </span>
  )
}
