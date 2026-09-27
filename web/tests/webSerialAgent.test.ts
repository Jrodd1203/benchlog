// Tests for the Web Serial agent client against a fake ESP32 (no browser or hardware needed).
// Run with: npm test   (Node's built-in test runner; Node 23.6+ runs TypeScript directly)

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { AgentError, WebSerialAgent, type SerialPortLike } from '../src/serial/webSerialAgent.ts'

const FAST = { readyTimeoutMs: 30, timeoutMs: 50, i2cTimeoutMs: 50 }

/** A serial port wired to a fake firmware. Override `respond` per test; `sent` records every line. */
class FakeEsp32 implements SerialPortLike {
  readable: ReadableStream<Uint8Array> | null = null
  writable: WritableStream<Uint8Array> | null = null
  sent: string[] = []
  closed = false
  inFlight = 0
  maxInFlight = 0
  private controller!: ReadableStreamDefaultController<Uint8Array>
  private readonly boot: string | null

  constructor(boot: string | null = '{"ready":true,"agent":"0.1.0"}\r\n') {
    this.boot = boot
  }

  respond(line: string): string | null {
    const [id, command, ...args] = line.split(' ')
    const reply: Record<string, unknown> = { id: Number(id), ok: true, cmd: command }
    if (command === 'PING') reply.uptime_ms = 1234
    else if (command === 'HELLO') Object.assign(reply, { board: 'esp32', agent: '0.1.0', pins: [4, 18, 21] })
    else if (command === 'PROBE') reply.pins = Object.fromEntries((args.length ? args : ['4', '18', '21']).map((p) => [p, 'floating']))
    else if (command === 'I2C') Object.assign(reply, { sda: 21, scl: 22, devices: ['0x76'] })
    else if (command === 'READ') {
      if (args[0] === '5') return JSON.stringify({ id: Number(id), ok: false, error: 'unsafe_pin' }) + '\r\n'
      Object.assign(reply, { pin: Number(args[0]), digital: 1, analog_mv: 3100 })
    } else return JSON.stringify({ id: Number(id), ok: false, error: 'unknown_command' }) + '\r\n'
    return JSON.stringify(reply) + '\r\n'
  }

  push(text: string): void {
    this.controller.enqueue(new TextEncoder().encode(text))
  }

  unplug(): void {
    this.controller.error(new Error('device lost'))
  }

  async open(): Promise<void> {
    this.readable = new ReadableStream<Uint8Array>({ start: (c) => void (this.controller = c) })
    this.writable = new WritableStream<Uint8Array>({
      write: async (chunk) => {
        const line = new TextDecoder().decode(chunk).replace(/\n$/, '')
        this.sent.push(line)
        this.inFlight++
        this.maxInFlight = Math.max(this.maxInFlight, this.inFlight)
        await new Promise((r) => setTimeout(r, 2))
        const reply = this.respond(line)
        this.inFlight--
        if (reply) this.push(reply)
      },
    })
    if (this.boot) setTimeout(() => this.push(this.boot!), 5)
  }

  async close(): Promise<void> {
    this.closed = true
  }

  getInfo() {
    return { usbVendorId: 0x10c4, usbProductId: 0xea60 }
  }
}

test('connect waits for the boot line, then says HELLO', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  const hello = await agent.connect()
  assert.equal(hello.agent, '0.1.0')
  assert.deepEqual(fake.sent, ['1 HELLO'])
  assert.equal(agent.connected, true)
  await agent.disconnect()
  assert.equal(fake.closed, true)
})

test('connect still works when the board does not reset on open (no boot line)', async () => {
  const agent = new WebSerialAgent(new FakeEsp32(null), FAST)
  assert.equal((await agent.connect()).board, 'esp32')
})

test('commands send args, and replies are typed', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  await agent.connect()
  assert.deepEqual((await agent.probe([18, 21])).pins, { '18': 'floating', '21': 'floating' })
  assert.deepEqual((await agent.i2c()).devices, ['0x76'])
  assert.equal((await agent.read(34)).analog_mv, 3100)
  assert.equal((await agent.ping()).uptime_ms, 1234)
  assert.deepEqual(fake.sent, ['1 HELLO', '2 PROBE 18 21', '3 I2C', '4 READ 34', '5 PING'])
})

test('noise, other ids, and replies split across reads are handled', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  await agent.connect()
  const normal = fake.respond.bind(fake)
  fake.respond = (line) => {
    const id = Number(line.split(' ')[0])
    const reply = normal(line)!
    fake.push('\xff\xfe garbage\r\n[1,2]\n\n' + JSON.stringify({ id: id - 1, ok: true, uptime_ms: 1 }) + '\n')
    fake.push(reply.slice(0, 7)) // half a line...
    setTimeout(() => fake.push(reply.slice(7)), 5) // ...and the rest later
    return null
  }
  assert.equal((await agent.ping()).uptime_ms, 1234)
})

test('a timeout is retried once with a new id', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  await agent.connect()
  const normal = fake.respond.bind(fake)
  let dropped = false
  fake.respond = (line) => (line.includes('PING') && !dropped ? ((dropped = true), null) : normal(line))
  assert.equal((await agent.ping()).uptime_ms, 1234)
  assert.deepEqual(fake.sent.slice(1), ['2 PING', '3 PING'])
})

test('two timeouts raise', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  await agent.connect()
  fake.respond = () => null
  await assert.rejects(agent.ping(), (e: unknown) => e instanceof AgentError && /no reply to PING/.test(e.message))
  assert.deepEqual(fake.sent.slice(1), ['2 PING', '3 PING'])
})

test('ok:false and bad_request replies raise with the code', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  await agent.connect()
  await assert.rejects(agent.read(5), (e: unknown) => e instanceof AgentError && e.code === 'unsafe_pin')
  fake.respond = () => '{"id":null,"ok":false,"error":"bad_request"}\r\n'
  await assert.rejects(agent.ping(), (e: unknown) => e instanceof AgentError && e.code === 'bad_request')
})

test('one request at a time', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, FAST)
  await agent.connect()
  await Promise.all(Array.from({ length: 6 }, () => agent.ping()))
  assert.equal(fake.maxInFlight, 1)
  assert.equal(fake.sent.length, 7)
})

test('unplugging rejects the pending request and reports the disconnect', async () => {
  const fake = new FakeEsp32()
  const agent = new WebSerialAgent(fake, { ...FAST, timeoutMs: 2000 })
  await agent.connect()
  const reasons: string[] = []
  agent.onDisconnect((r) => reasons.push(r))
  fake.respond = () => null // never answers
  const pending = agent.ping()
  setTimeout(() => fake.unplug(), 10)
  await assert.rejects(pending, (e: unknown) => e instanceof AgentError && /lost the ESP32/.test(e.message))
  assert.equal(agent.connected, false)
  assert.deepEqual(reasons, ['disconnected'])
  await assert.rejects(agent.ping(), /not connected/)
})
