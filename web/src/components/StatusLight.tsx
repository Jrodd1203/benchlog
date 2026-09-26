import { useEffect, useState } from 'react'
import { getSerialStatus } from '../api'
import type { SerialState } from '../types'

const LABEL: Record<SerialState, string> = {
  connected: 'ESP32 connected',
  disconnected: 'ESP32 not connected',
  unknown: 'ESP32 status unavailable',
}

/** Polls GET /api/serial/status and shows a coloured dot. */
export function StatusLight() {
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

  return (
    <span className={`status-light ${state}`} title={port ?? undefined}>
      <span className="dot" aria-hidden="true" />
      {LABEL[state]}
    </span>
  )
}
