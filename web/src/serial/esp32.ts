// The browser's ESP32 connection (Web Serial), shared by the whole app.
//
// React components read it with useEsp32(); scans call takeReadings() to send what the board senses
// along with POST /api/scan.

import { useSyncExternalStore } from 'react'
import type { BrowserSerialState, SerialReadings } from '../types'
import { AgentError, WebSerialAgent, webSerial, type HelloResult, type SerialPortLike } from './webSerialAgent'

export interface Esp32State {
  state: BrowserSerialState
  /** e.g. "browser (USB 10c4:ea60)", sent as the readings' port label. */
  label: string | null
  hello: HelloResult | null
  /** Why the last connect failed or the connection dropped. */
  error: string | null
}

const PING_EVERY_MS = 5000

let agent: WebSerialAgent | null = null
let pingTimer: ReturnType<typeof setInterval> | null = null
let snapshot: Esp32State = {
  state: webSerial() ? 'disconnected' : 'unsupported',
  label: null,
  hello: null,
  error: null,
}
const listeners = new Set<() => void>()

function set(next: Partial<Esp32State>): void {
  snapshot = { ...snapshot, ...next }
  listeners.forEach((l) => l())
}

export function getEsp32(): Esp32State {
  return snapshot
}

export function useEsp32(): Esp32State {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getEsp32,
  )
}

function labelFor(port: SerialPortLike): string {
  const { usbVendorId, usbProductId } = port.getInfo()
  const hex = (n?: number) => (n === undefined ? '?' : n.toString(16).padStart(4, '0'))
  return usbVendorId === undefined ? 'browser' : `browser (USB ${hex(usbVendorId)}:${hex(usbProductId)})`
}

/** Plain-English reason a connect failed. */
function explain(e: unknown): string {
  if (e instanceof AgentError) return `${e.message}. Is the benchlog agent firmware flashed?`
  if (e instanceof DOMException) {
    if (e.name === 'InvalidStateError') {
      // Web Serial: the port is already open in this browser.
      return 'The ESP32 is already open in another tab (or in this page before a reload). Close other benchlog tabs or reload this page, then retry.'
    }
    if (e.name === 'NetworkError') {
      // Web Serial: another program has the port.
      return 'The ESP32’s port is busy. Close the serial monitor or `benchlog serve`, then retry.'
    }
    if (e.name === 'SecurityError') return 'The browser blocked access to the serial port (the page must be served over HTTPS).'
  }
  return e instanceof Error ? e.message : String(e)
}

function stopPinging(): void {
  if (pingTimer !== null) clearInterval(pingTimer)
  pingTimer = null
}

/** Ask for a port the first time; afterwards reuse the one the user already allowed. */
export async function connectEsp32(): Promise<void> {
  const serial = webSerial()
  if (!serial) {
    set({ state: 'unsupported' })
    return
  }
  if (snapshot.state === 'connecting' || snapshot.state === 'connected') return
  let port: SerialPortLike
  try {
    port = (await serial.getPorts())[0] ?? (await serial.requestPort())
  } catch (e) {
    // NotFoundError: the user closed the port picker without choosing one.
    set({ error: e instanceof DOMException && e.name === 'NotFoundError' ? null : explain(e) })
    return
  }
  set({ state: 'connecting', error: null })
  const next = new WebSerialAgent(port)
  try {
    const hello = await next.connect()
    agent = next
    next.onDisconnect((reason) => {
      if (agent !== next) return
      agent = null
      stopPinging()
      set({ state: 'disconnected', hello: null, error: reason === 'closed' ? null : 'The ESP32 was disconnected.' })
    })
    set({ state: 'connected', label: labelFor(port), hello, error: null })
    pingTimer = setInterval(() => {
      next.ping().catch(() => next.disconnect('not answering'))
    }, PING_EVERY_MS)
  } catch (e) {
    set({ state: 'disconnected', error: explain(e) })
  }
}

export async function disconnectEsp32(): Promise<void> {
  await agent?.disconnect()
}

/**
 * What the ESP32 senses right now, for a scan: every safe pin, then the I2C bus. Null readings when
 * the browser isn't connected; `error` when it is but the probe failed (the scan goes camera-only).
 */
export async function takeReadings(): Promise<{ readings: SerialReadings | null; error: string | null }> {
  const current = agent
  if (!current?.connected) return { readings: null, error: null }
  try {
    const probe = await current.probe()
    let i2c: string[] | null = null
    try {
      i2c = (await current.i2c()).devices
    } catch {
      // pins are still worth checking; null means "I2C wasn't scanned"
    }
    return {
      readings: {
        probed_at: new Date().toISOString(),
        port: snapshot.label,
        agent: current.info?.agent ?? null,
        pins: probe.pins,
        i2c,
      },
      error: null,
    }
  } catch (e) {
    return { readings: null, error: e instanceof Error ? e.message : String(e) }
  }
}

// The device was unplugged: fail fast instead of waiting for a request to time out.
webSerial()?.addEventListener('disconnect', (event) => {
  if (agent && (event.target as unknown) === agent.port) void agent.disconnect('unplugged')
})

// Release the port when the page goes away, so a reload (or `benchlog serve`) can open it again.
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => {
    void agent?.disconnect()
  })
}
