// All data access for the web UI goes through this module.
//
// Real endpoints are called with fetch (proxied to `benchlog serve` on :8000). When the backend
// isn't running we fall back to the example files so the UI still renders. Anything marked
// NOT WIRED has no backend endpoint yet; it returns placeholder data and must be replaced.

import movedWireExample from './examples/circuits/moved-wire.json'
import workingExample from './examples/circuits/working.json'
import { stripOf } from './board/geometry'
import { getEsp32, takeReadings } from './serial/esp32'
import { BUILD_STAGES } from './mock/buildStages'
import type {
  AcceptResponse,
  Circuit,
  CommitResponse,
  DiffResponse,
  Hole,
  Observation,
  PortInfo,
  ProjectSummary,
  ScanResponse,
  SerialSnapshot,
  SerialState,
  SerialStatus,
  StatusResponse,
  TimelineEntry,
} from './types'

/** Where a piece of data came from, so the UI can say when it's showing example data. */
export type Source = 'api' | 'example'

export interface Loaded<T> {
  data: T
  source: Source
}

/** Error from the backend; `message` is its plain-English `detail` when it sent one. */
export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// The open project (its id from GET/POST /api/projects), sent as `?project=` on every request so
// the backend's `get_project()` resolves the right folder under PROJECTS_ROOT. Null means "the
// single project `benchlog serve` was started in" (its BENCHLOG_PROJECT/cwd fallback).
let activeProjectId: string | null = null

export function setActiveProject(id: string | null): void {
  activeProjectId = id
}

function withProject(path: string): string {
  if (activeProjectId === null) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}project=${encodeURIComponent(activeProjectId)}`
}

// Empty in dev (the Vite dev server proxies /api). Set VITE_API_URL at build time when the API is
// on another origin, e.g. a separately deployed backend.
const API_BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_BASE + withProject(path), {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Can’t reach the benchlog server. Is `benchlog serve` running?')
  }
  if (!res.ok) {
    let detail = `${method} ${path} failed (${res.status})`
    try {
      const data = await res.json()
      if (typeof data?.detail === 'string') detail = data.detail
      // 422 validation errors: [{loc: ["body", "simulate"], msg: "..."}, ...]
      else if (Array.isArray(data?.detail) && data.detail.length > 0) {
        detail = data.detail
          .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).filter((p) => p !== 'body').join('.')}: ${e.msg ?? 'invalid'}`)
          .join('; ')
      }
    } catch {
      // not JSON (e.g. the dev proxy's 502 page); keep the generic message
    }
    if (res.status === 502 || res.status === 504) detail = "Can’t reach the benchlog server. Is `benchlog serve` running?"
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204 || res.status === 205) return undefined as T
  return (await res.json()) as T
}

const getJson = <T>(path: string) => request<T>('GET', path)

/** The working circuit (GET /api/circuit), or the example circuit when the API is down. */
export async function getCircuit(): Promise<Loaded<Circuit>> {
  try {
    return { data: await getJson<Circuit>('/api/circuit'), source: 'api' }
  } catch {
    return { data: workingExample as unknown as Circuit, source: 'example' }
  }
}

/** ESP32 agent connection (GET /api/serial/status); 'unknown' when the server isn't reachable. */
export async function getSerialStatus(): Promise<{ state: SerialState; status: SerialStatus | null }> {
  try {
    const s = await getJson<SerialStatus>('/api/serial/status')
    return { state: s.connected ? 'connected' : 'disconnected', status: s }
  } catch {
    return { state: 'unknown', status: null }
  }
}

export const getSerialPorts = () => getJson<PortInfo[]>('/api/serial/ports')

/** Opens the port and says HELLO. Takes a few seconds: the ESP32 reboots when the port opens. */
export const connectSerial = (port: string) => request<SerialStatus>('POST', '/api/serial/connect', { port })

export const probeSerial = () => request<SerialSnapshot>('POST', '/api/serial/probe')

// ── Status, scan, review, commit ─────────────────────────────────────────────────────────────

export const getStatus = () => getJson<StatusResponse>('/api/status')

/** POST /api/scan. `simulate` pretends the board looks like that circuit (no camera needed). */
export async function scan(options: { simulate?: Circuit } = {}): Promise<ScanResponse> {
  // A hosted backend can't reach the ESP32 on this laptop, so if the browser is connected to it
  // (Web Serial) we probe it here and send the readings with the scan. Otherwise the server uses its
  // own ESP32 (local `benchlog serve`) or scans camera-only.
  const { readings, error } = await takeReadings()
  const result = await request<ScanResponse>('POST', '/api/scan', readings ? { ...options, serial: readings } : options)
  if (error) result.warnings = [...result.warnings, `serial: not checked (${error})`]
  saveLastScan(result)
  return result
}

/**
 * Repeatable demo scan (no camera): first record the board as matching the current circuit
 * (`sync`), then scan the example board where the pot signal wire moved A4 -> A12. Without the
 * sync, a second demo scan finds nothing, because the backend compares against the last scan.
 */
export async function demoScan(): Promise<ScanResponse> {
  const current = await request<Circuit>('GET', '/api/circuit')
  await request<ScanResponse>('POST', '/api/scan', { simulate: current, sync: true })
  return scan({ simulate: movedWireExample as unknown as Circuit })
}

export const getPendingObservations = () => getJson<Observation[]>('/api/observations?pending_only=true')

/** Apply observations to the working circuit, all or nothing. No ids = every pending one. */
export const acceptObservations = (ids?: string[]) =>
  request<AcceptResponse>('POST', '/api/observations/accept', { ids: ids ?? null })

export const rejectObservations = (ids?: string[]) =>
  request<Observation[]>('POST', '/api/observations/reject', { ids: ids ?? null })

/** "Pick the right hole": set a pending wire's ends, e.g. { b: 'J45' }. */
export const editObservation = (id: string, ends: Partial<Record<'a' | 'b', Hole>>) =>
  request<Observation>('PATCH', `/api/observations/${encodeURIComponent(id)}`, { ends })

/**
 * The server checks the circuit against the ESP32 before committing. If this browser is connected to
 * the ESP32 (Web Serial), its readings are sent along, since a hosted server can't reach it. With
 * `force`, commits even if the check fails (recorded as "ESP32-Check: failed (forced)").
 */
export async function commit(message: string, force = false): Promise<CommitResponse> {
  const { readings } = await takeReadings()
  return request<CommitResponse>('POST', '/api/commit', { message, force, ...(readings ? { serial: readings } : {}) })
}

/** GET /api/diff. Omit `old` for HEAD, omit `new` for the working circuit. */
export function getDiff(old?: string, next?: string): Promise<DiffResponse> {
  const q = new URLSearchParams()
  if (old) q.set('old', old)
  if (next) q.set('new', next)
  const qs = q.toString()
  return getJson<DiffResponse>(`/api/diff${qs ? `?${qs}` : ''}`)
}

// Verdicts only come back in the scan response (the backend doesn't store them yet), so the last
// scan is kept here and in localStorage to survive a reload. TODO: drop this once GET
// /api/observations returns verdicts (see docs/api-endpoints-for-ui.md).
const LAST_SCAN_KEY = 'benchlog:last-scan'
let lastScan: ScanResponse | null = null

function saveLastScan(result: ScanResponse) {
  lastScan = result
  try {
    localStorage.setItem(LAST_SCAN_KEY, JSON.stringify(result))
  } catch {
    // storage unavailable (private window); in-memory copy still works this session
  }
}

export function getLastScan(): ScanResponse | null {
  if (lastScan) return lastScan
  try {
    const raw = localStorage.getItem(LAST_SCAN_KEY)
    lastScan = raw ? (JSON.parse(raw) as ScanResponse) : null
  } catch {
    lastScan = null
  }
  return lastScan
}

// ── Timeline ─────────────────────────────────────────────────────────────────────────────────

interface HistoryEntryResponse {
  commit: { sha: string; short_sha: string; author: string; date: string; subject: string }
  lines: string[]
  checkpoint?: { label: string; note?: string }
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
    const entries = history.reverse().map(({ commit, lines, checkpoint }) => ({
      sha: commit.sha,
      shortSha: commit.short_sha,
      message: commit.subject,
      author: commit.author,
      date: commit.date,
      lines,
      electrical: lines.some((l) => /\bconnected\b|\bdisconnected\b/.test(l)),
      checkpoint: checkpoint ?? undefined,
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

// ── Remote community circuits ─────────────────────────────────────────────────────────────────
//
// No auth — public repos only. GitHub API: 60 req/hour per IP unauthenticated.
// Commit list via api.github.com; file contents via raw.githubusercontent.com.

import type { RemoteCommit } from './types'

// GitHub allows ~60 unauthenticated API requests an hour per IP, and a demo room shares one IP.
// So: circuits are cached for good (a commit's file never changes), commit lists are reused for a
// couple of minutes and then revalidated with an ETag (an unchanged 304 doesn't count against the
// limit), and if the limit is hit anyway the last saved list is shown instead of an error.

const EXPLORE_CACHE = 'benchlog:explore:'
const HISTORY_FRESH_MS = 2 * 60 * 1000

interface CachedHistory {
  at: number
  etag: string | null
  commits: RemoteCommit[]
}

function readCache<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(EXPLORE_CACHE + key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null // storage unavailable (private window): just fetch
  }
}

function writeCache(key: string, value: unknown): void {
  try {
    localStorage.setItem(EXPLORE_CACHE + key, JSON.stringify(value))
  } catch {
    // storage full or unavailable: caching is best-effort
  }
}

/** "owner/repo", "github.com/owner/repo" or a full GitHub URL (with or without .git or a path). */
export function parseRepo(input: string): { owner: string; repo: string } | null {
  const text = input.trim().replace(/^https?:\/\//, '').replace(/^(www\.)?github\.com\//, '')
  const m = /^([A-Za-z0-9-]+)\/([A-Za-z0-9._-]+?)(?:\.git)?(?:[/?#].*)?$/.exec(text)
  return m ? { owner: m[1], repo: m[2] } : null
}

function rateLimitMessage(res: Response): string {
  const reset = Number(res.headers.get('x-ratelimit-reset'))
  const when = reset ? ` after ${new Date(reset * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : ' in a while'
  return `GitHub's limit for anonymous requests was reached. Try again${when}.`
}

/**
 * Commits that touched benchlog/circuit.json in a public repo, newest first.
 * Throws a user-readable message on rate-limit (403/429, with nothing cached) or not-found (404).
 */
export async function fetchRemoteHistory(owner: string, repo: string): Promise<RemoteCommit[]> {
  const key = `history:${owner}/${repo}`.toLowerCase()
  const cached = readCache<CachedHistory>(key)
  if (cached && Date.now() - cached.at < HISTORY_FRESH_MS) return cached.commits

  const headers: Record<string, string> = { Accept: 'application/vnd.github.v3+json' }
  if (cached?.etag) headers['If-None-Match'] = cached.etag
  let res: Response
  try {
    res = await fetch(
      `https://api.github.com/repos/${owner}/${repo}/commits?path=benchlog%2Fcircuit.json&per_page=100`,
      { headers },
    )
  } catch (err) {
    if (cached) return cached.commits // offline: last saved copy
    throw err
  }
  if (res.status === 304 && cached) {
    writeCache(key, { ...cached, at: Date.now() })
    return cached.commits
  }
  if (res.status === 403 || res.status === 429) {
    if (cached) return cached.commits
    throw new Error(rateLimitMessage(res))
  }
  if (res.status === 404) throw new Error('Repository not found (it must be public), or circuit.json isn’t committed yet.')
  if (!res.ok) throw new Error(`GitHub API error (${res.status})`)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const data = (await res.json()) as any[]
  const commits = data.map((c) => ({
    sha: c.sha as string,
    shortSha: (c.sha as string).slice(0, 7),
    message: (c.commit.message as string).split('\n')[0],
    author: c.commit.author.name as string,
    date: c.commit.author.date as string,
  }))
  writeCache(key, { at: Date.now(), etag: res.headers.get('etag'), commits } satisfies CachedHistory)
  return commits
}

export async function fetchRemoteCircuitAt(owner: string, repo: string, sha: string): Promise<Circuit | null> {
  // A commit's circuit never changes, so once fetched it's kept for good.
  const key = `circuit:${owner}/${repo}@${sha}`.toLowerCase()
  const cached = readCache<Circuit>(key)
  if (cached) return cached
  try {
    const res = await fetch(`https://raw.githubusercontent.com/${owner}/${repo}/${sha}/benchlog/circuit.json`)
    if (!res.ok) return null
    const data = await res.json()
    if (!data || typeof data !== 'object' || !('wires' in data) || !('components' in data)) return null
    writeCache(key, data)
    return data as Circuit
  } catch {
    return null
  }
}

// ── Projects ─────────────────────────────────────────────────────────────────────────────────
//
// GET/POST /api/projects create real project folders (a git repo each, same layout as
// `benchlog init`) under the server's PROJECTS_ROOT. When the backend isn't reachable at all
// (or is an older build without these routes), we fall back to a browser-local placeholder list
// so the UI still works standalone.

const PLACEHOLDER_PROJECTS: ProjectSummary[] = [
  { id: 'demo', name: 'ESP32 pot demo', board: 'bb830', updated: null, setupComplete: true },
]

const LOCAL_PROJECTS_KEY = 'benchlog:local-projects'

function loadLocalProjects(): ProjectSummary[] {
  try {
    const raw = localStorage.getItem(LOCAL_PROJECTS_KEY)
    return raw ? (JSON.parse(raw) as ProjectSummary[]) : []
  } catch {
    return []
  }
}

function saveLocalProjects(projects: ProjectSummary[]) {
  try {
    localStorage.setItem(LOCAL_PROJECTS_KEY, JSON.stringify(projects))
  } catch {
    // storage unavailable (private window); new project still shows for this session
  }
}

function noBackend(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 0 || err.status === 404)
}

/** The real project list (GET /api/projects), or the placeholder + local ones when it's unreachable. */
export async function listProjects(): Promise<ProjectSummary[]> {
  try {
    return await getJson<ProjectSummary[]>('/api/projects')
  } catch (err) {
    if (!noBackend(err)) throw err
    return [...PLACEHOLDER_PROJECTS, ...loadLocalProjects()]
  }
}

/**
 * Creates a real project folder + git repo (POST /api/projects). Falls back to this browser's
 * localStorage only when there's no backend to talk to.
 */
export async function createProject(input: { name: string; board: string }): Promise<ProjectSummary> {
  try {
    return await request<ProjectSummary>('POST', '/api/projects', input)
  } catch (err) {
    // Only fall back to local storage when there's no backend to talk to (network failure, or the
    // route doesn't exist yet). A real error from an existing endpoint (validation failure, 500,
    // etc.) must surface to the user instead of being silently treated as success.
    if (!noBackend(err)) throw err
    const project: ProjectSummary = {
      id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: input.name,
      board: input.board,
      updated: null,
      setupComplete: false,
    }
    saveLocalProjects([...loadLocalProjects(), project])
    return project
  }
}

// ── Build guide / checkpoints ─────────────────────────────────────────────────────────────────

export async function markCheckpoint(sha: string, label: string, note = ''): Promise<void> {
  await request('POST', `/api/commits/${sha}/checkpoint`, { label, note })
}

export async function unmarkCheckpoint(sha: string): Promise<void> {
  await request('DELETE', `/api/commits/${sha}/checkpoint`)
}

// Dev only: hand the browser console this module as the app itself uses it (same open project and
// same ESP32 connection), e.g. to test a scan without a camera:
//   const c = (await benchlog.getCircuit()).data; c.wires.push({ id: 'w9', a: 'J7', b: 'R-7' })
//   await benchlog.scan({ simulate: c })
if (import.meta.env.DEV && typeof window !== 'undefined') {
  Object.assign(window, { benchlog: { getCircuit, scan, acceptObservations, rejectObservations, commit, esp32: getEsp32 } })
}
