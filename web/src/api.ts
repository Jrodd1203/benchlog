// All data access for the web UI goes through this module.
//
// Real endpoints are called with fetch (proxied to `benchlog serve` on :8000). When the backend
// isn't running we fall back to the example files so the UI still renders. Anything marked
// NOT WIRED has no backend endpoint yet; it returns placeholder data and must be replaced.

import workingExample from '../../examples/circuits/working.json'
import { stripOf } from './board/geometry'
import { BUILD_STAGES } from './mock/buildStages'
import type { AcceptResponse, Circuit, ProjectSummary, ScanResponse, SerialState, StatusResponse, TimelineEntry } from './types'

/** Where a piece of data came from, so the UI can say when it's showing example data. */
export type Source = 'api' | 'example'

export interface Loaded<T> {
  data: T
  source: Source
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return (await res.json()) as T
}

/** The working circuit (GET /api/circuit), or the example circuit when the API is down. */
export async function getCircuit(): Promise<Loaded<Circuit>> {
  try {
    return { data: await getJson<Circuit>('/api/circuit'), source: 'api' }
  } catch {
    return { data: workingExample as unknown as Circuit, source: 'example' }
  }
}

interface SerialStatusResponse {
  connected: boolean
  port: string | null
  last_error: string | null
}

/** ESP32 agent connection (GET /api/serial/status). */
export async function getSerialStatus(): Promise<{ state: SerialState; port: string | null }> {
  try {
    const s = await getJson<SerialStatusResponse>('/api/serial/status')
    return { state: s.connected ? 'connected' : 'disconnected', port: s.port }
  } catch {
    return { state: 'unknown', port: null }
  }
}

// ── Timeline ─────────────────────────────────────────────────────────────────────────────────

interface HistoryEntryResponse {
  commit: { sha: string; short_sha: string; author: string; date: string; subject: string }
  lines: string[]
}

const circuitCache = new Map<string, Circuit>()

/**
 * Every commit that changed the circuit, OLDEST first (GET /api/history returns newest first).
 * Falls back to the mock build history when the API is down or the project has no commits.
 */
export async function getTimeline(): Promise<Loaded<TimelineEntry[]>> {
  try {
    const history = await getJson<HistoryEntryResponse[]>('/api/history')
    if (history.length === 0) throw new Error('no commits yet')
    const entries = history.reverse().map(({ commit, lines }) => ({
      sha: commit.sha,
      shortSha: commit.short_sha,
      message: commit.subject,
      author: commit.author,
      date: commit.date,
      lines,
      electrical: lines.some((l) => /\bconnected\b|\bdisconnected\b/.test(l)),
    }))
    return { data: entries, source: 'api' }
  } catch {
    return { data: mockTimeline(), source: 'example' }
  }
}

/** The committed circuit at a revision (GET /api/circuit/{sha}), cached. */
export async function getCircuitAt(sha: string): Promise<Circuit> {
  const cached = circuitCache.get(sha)
  if (cached) return cached
  const circuit = await getJson<Circuit>(`/api/circuit/${sha}`)
  circuitCache.set(sha, circuit)
  return circuit
}

function mockTimeline(): TimelineEntry[] {
  return BUILD_STAGES.map((s, i) => {
    circuitCache.set(s.sha, s.circuit)
    const prev = i > 0 ? BUILD_STAGES[i - 1].circuit : { board: 'bb830', components: [], wires: [] }
    const { lines, electrical } = describeChanges(prev, s.circuit)
    return {
      sha: s.sha,
      shortSha: s.sha.slice(0, 7),
      message: s.message,
      author: s.author,
      date: s.date,
      lines,
      electrical,
      check: s.check,
      tested: s.tested,
      note: s.note,
    }
  })
}

/** Rough client-side stand-in for the backend's `describe(diff(...))`, used for mock data only. */
function describeChanges(prev: Circuit, curr: Circuit): { lines: string[]; electrical: boolean } {
  const lines: string[] = []
  let electrical = false
  const prevParts = new Map(prev.components.map((c) => [c.id, c]))
  const currParts = new Map(curr.components.map((c) => [c.id, c]))
  for (const [id, c] of currParts) if (!prevParts.has(id)) lines.push(`${id} added (${c.value ?? c.type})`)
  for (const id of prevParts.keys()) if (!currParts.has(id)) lines.push(`${id} removed`)
  const prevWires = new Map(prev.wires.map((w) => [w.id, w]))
  for (const w of curr.wires) {
    const old = prevWires.get(w.id)
    if (!old) {
      lines.push(`${w.id} added: ${w.a} → ${w.b}${w.label ? ` (${w.label})` : ''}`)
    } else if (old.a !== w.a || old.b !== w.b) {
      const crossesStrip = stripOf(old.a) !== stripOf(w.a) || stripOf(old.b) !== stripOf(w.b)
      electrical ||= crossesStrip
      const [from, to] = old.a !== w.a ? [old.a, w.a] : [old.b, w.b]
      lines.push(`${w.id} moved: ${from} → ${to}${crossesStrip ? ' (connected to a different strip)' : ' (same strip)'}`)
    }
  }
  for (const id of prevWires.keys()) if (!curr.wires.some((w) => w.id === id)) lines.push(`${id} removed`)
  return { lines, electrical }
}

export async function getStatus(): Promise<StatusResponse | null> {
  try { return await getJson<StatusResponse>('/api/status') }
  catch { return null }
}

export async function postScan(simulate?: Circuit): Promise<ScanResponse> {
  const body: Record<string, unknown> = {}
  if (simulate) body.simulate = simulate
  const res = await fetch('/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`scan failed: ${res.status}`)
  return res.json() as Promise<ScanResponse>
}

export async function acceptObservations(ids: string[] | null): Promise<AcceptResponse> {
  const res = await fetch('/api/observations/accept', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  if (!res.ok) throw new Error(`accept failed: ${res.status}`)
  return res.json() as Promise<AcceptResponse>
}

export async function rejectObservations(ids: string[] | null): Promise<void> {
  const res = await fetch('/api/observations/reject', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  if (!res.ok) throw new Error(`reject failed: ${res.status}`)
}

export async function postCommit(message: string): Promise<{ commit: { sha: string; short_sha: string; subject: string } }> {
  const res = await fetch('/api/commit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, firmware: [] }),
  })
  if (!res.ok) throw new Error(`commit failed: ${res.status}`)
  return res.json()
}

// ── NOT WIRED: no backend endpoint yet (Person 1: project list/create/open) ──────────────────

const PLACEHOLDER_PROJECTS: ProjectSummary[] = [
  { id: 'demo', name: 'ESP32 pot demo', board: 'bb830', updated: null, setupComplete: true },
]

/** NOT WIRED: needs GET /api/projects. Returns a placeholder list. */
export async function listProjects(): Promise<ProjectSummary[]> {
  return PLACEHOLDER_PROJECTS
}
