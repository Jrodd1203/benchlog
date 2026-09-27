// Talks to the benchlog ESP32 serial agent (firmware in agent/) from the browser, over Web Serial.
//
// A hosted backend can't reach an ESP32 plugged into the user's laptop, so the browser reads it and
// sends the readings with each scan. This mirrors the Python client (src/benchlog/serial/agent_client.py):
// send "<id> <COMMAND> [args]\n", get back one line of JSON with the same "id" and "ok". Anything
// else on the line (the boot banner, noise, late replies to a retried request) is skipped.

import type { PinState } from '../types'

// ── The bits of the Web Serial API we use (TypeScript's DOM types don't include it yet) ──────────

export interface SerialPortInfo {
  usbVendorId?: number
  usbProductId?: number
}

export interface SerialPortLike {
  open(options: { baudRate: number }): Promise<void>
  close(): Promise<void>
  readonly readable: ReadableStream<Uint8Array> | null
  readonly writable: WritableStream<Uint8Array> | null
  getInfo(): SerialPortInfo
}

export interface SerialLike extends EventTarget {
  getPorts(): Promise<SerialPortLike[]>
  requestPort(options?: object): Promise<SerialPortLike>
}

/** navigator.serial, or null in browsers without Web Serial (Firefox, Safari). */
export function webSerial(): SerialLike | null {
  return typeof navigator !== 'undefined' && 'serial' in navigator
    ? ((navigator as unknown as { serial: SerialLike }).serial)
    : null
}

export const isSupported = () => webSerial() !== null

// ── Results ───────────────────────────────────────────────────────────────────────────────────

export interface HelloResult {
  board: string
  /** Firmware version, e.g. "0.1.0". */
  agent: string
  /** GPIOs the agent may probe. */
  pins: number[]
}

export interface ProbeResult {
  /** GPIO (as a string key, as the firmware sends it) -> what it senses. */
  pins: Record<string, PinState>
}

export interface I2cResult {
  sda: number
  scl: number
  devices: string[]
}

export interface ReadResult {
  pin: number
  digital: number
  analog_mv: number | null
}

export class AgentError extends Error {
  /** The firmware's error code for ok:false replies, e.g. "unsafe_pin". */
  readonly code: string | null
  constructor(message: string, code: string | null = null) {
    super(message)
    this.code = code
  }
}

// ── Client ────────────────────────────────────────────────────────────────────────────────────

export const BAUD = 115200
const READY_TIMEOUT_MS = 3000 // the ESP32 resets when the port opens and boots in about a second
const TIMEOUT_MS = 2000
const I2C_TIMEOUT_MS = 6000 // a shorted SDA/SCL makes every address wait for a timeout (same as the Python client)

type Reply = Record<string, unknown>

interface Pending {
  id: number
  resolve: (reply: Reply) => void
  reject: (error: Error) => void
}

export interface AgentOptions {
  readyTimeoutMs?: number
  timeoutMs?: number
  i2cTimeoutMs?: number
}

export class WebSerialAgent {
  readonly port: SerialPortLike
  info: HelloResult | null = null
  private readonly options: Required<AgentOptions>
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  private open_ = false
  private nextId = 1
  private pending: Pending | null = null
  private onReady: (() => void) | null = null
  private queue: Promise<unknown> = Promise.resolve()
  private readonly disconnectListeners = new Set<(reason: string) => void>()

  constructor(port: SerialPortLike, options: AgentOptions = {}) {
    this.port = port
    this.options = {
      readyTimeoutMs: options.readyTimeoutMs ?? READY_TIMEOUT_MS,
      timeoutMs: options.timeoutMs ?? TIMEOUT_MS,
      i2cTimeoutMs: options.i2cTimeoutMs ?? I2C_TIMEOUT_MS,
    }
  }

  get connected(): boolean {
    return this.open_
  }

  /** Called once when the connection is lost (unplugged, closed, or the read loop failed). */
  onDisconnect(listener: (reason: string) => void): () => void {
    this.disconnectListeners.add(listener)
    return () => this.disconnectListeners.delete(listener)
  }

  /**
   * Open the port, wait for the boot line, then say HELLO. Some USB adapters don't reset the board
   * on open, so a missing boot line isn't fatal: HELLO is what proves an agent is there.
   */
  async connect(): Promise<HelloResult> {
    await this.port.open({ baudRate: BAUD })
    this.open_ = true
    const ready = new Promise<void>((resolve) => {
      this.onReady = resolve
    })
    void this.readLoop()
    await Promise.race([ready, sleep(this.options.readyTimeoutMs)])
    this.onReady = null
    try {
      this.info = await this.hello()
    } catch (e) {
      await this.disconnect()
      throw e
    }
    return this.info
  }

  ping = () => this.request<{ uptime_ms: number }>('PING')
  hello = () => this.request<HelloResult>('HELLO')
  /** Probe the given GPIOs, or every safe pin if none are given. */
  probe = (pins: number[] = []) => this.request<ProbeResult>('PROBE', pins)
  i2c = () => this.request<I2cResult>('I2C', [], this.options.i2cTimeoutMs)
  read = (pin: number) => this.request<ReadResult>('READ', [pin])

  /** Close the port. `reason` is passed to onDisconnect listeners ('closed' when the user asked). */
  async disconnect(reason = 'closed'): Promise<void> {
    if (!this.open_) return
    this.lost(reason)
    try {
      await this.reader?.cancel()
    } catch {
      // already gone
    }
    try {
      await this.port.close()
    } catch {
      // unplugged: nothing left to release
    }
  }

  // ── Wire protocol ──

  /** Send a command and wait for its reply. One request at a time; a timeout is retried once. */
  private request<T>(command: string, args: (string | number)[] = [], timeoutMs = this.options.timeoutMs): Promise<T> {
    const run = async (): Promise<T> => {
      for (let attempt = 0; attempt < 2; attempt++) {
        if (!this.open_) throw new AgentError('ESP32 not connected')
        const id = this.nextId++
        const reply = this.waitFor(id, timeoutMs)
        await this.write([id, command, ...args].join(' ') + '\n')
        const result = await reply
        if (result === null) continue // timed out: retry with a new id
        if (result.ok !== true) {
          const code = typeof result.error === 'string' ? result.error : 'unknown_error'
          throw new AgentError(`agent rejected ${command}: ${code}`, code)
        }
        return result as T
      }
      throw new AgentError(`no reply to ${command} from the ESP32`)
    }
    const next = this.queue.then(run, run)
    this.queue = next.catch(() => {})
    return next
  }

  /** The reply with this id, or null after the timeout. Rejects if the connection is lost. */
  private waitFor(id: number, timeoutMs: number): Promise<Reply | null> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending?.id === id) this.pending = null
        resolve(null)
      }, timeoutMs)
      this.pending = {
        id,
        resolve: (reply) => {
          clearTimeout(timer)
          resolve(reply)
        },
        reject: (error) => {
          clearTimeout(timer)
          reject(error)
        },
      }
    })
  }

  private async write(line: string): Promise<void> {
    const writer = this.port.writable?.getWriter()
    if (!writer) throw new AgentError('ESP32 not connected')
    try {
      await writer.write(new TextEncoder().encode(line))
    } catch {
      this.lost('write failed')
      throw new AgentError('lost the ESP32 (write failed)')
    } finally {
      writer.releaseLock()
    }
  }

  private async readLoop(): Promise<void> {
    const decoder = new TextDecoder()
    let buffer = ''
    const reader = this.port.readable?.getReader()
    if (!reader) return this.lost('port not readable')
    this.reader = reader
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let newline: number
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline).replace(/\r$/, '').trim()
          buffer = buffer.slice(newline + 1)
          if (line) this.handleLine(line)
        }
      }
    } catch {
      // the device was unplugged or the stream errored
    } finally {
      reader.releaseLock()
      this.lost('disconnected')
    }
  }

  private handleLine(line: string): void {
    let message: unknown
    try {
      message = JSON.parse(line)
    } catch {
      return // noise on the line (e.g. the ROM bootloader's output)
    }
    if (typeof message !== 'object' || message === null || Array.isArray(message)) return
    const reply = message as Reply
    if (reply.ready === true) {
      this.onReady?.()
      return
    }
    const pending = this.pending
    if (!pending) return
    if (reply.id === pending.id) {
      this.pending = null
      pending.resolve(reply)
    } else if (reply.id === null && reply.error === 'bad_request') {
      // The agent couldn't read the id, so this can only be the request in flight.
      this.pending = null
      pending.reject(new AgentError('agent rejected the request: bad_request', 'bad_request'))
    }
    // Anything else is a late reply to an earlier attempt: skip it.
  }

  private lost(reason: string): void {
    if (!this.open_) return
    this.open_ = false
    const pending = this.pending
    this.pending = null
    pending?.reject(new AgentError(reason === 'closed' ? 'ESP32 disconnected' : `lost the ESP32 (${reason})`))
    this.disconnectListeners.forEach((listener) => listener(reason))
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))
