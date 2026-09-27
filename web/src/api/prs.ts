// Checks, branches, board state and local PRs (see docs/api-prs.md).
//
// Kept apart from api.ts so the two can change independently; requests go through the same
// `request()` (active project, error messages).

import { ApiError, request } from '../api'
import type { Circuit, CircuitDiff, Hole } from '../types'

// ── Checks ────────────────────────────────────────────────────────────────────────────────────

export type CheckStatus = 'pass' | 'fail' | 'needs_confirmation' | 'not_supported'

export interface CheckResult {
  check: string
  status: CheckStatus
  message: string
  /** Wires ('w5'), components ('pot1'), pins ('esp32.GPIO12') or observations involved. */
  ids: string[]
}

export interface CheckReport {
  status: 'pass' | 'fail' | 'needs_confirmation'
  /** Null for uncommitted changes. */
  commit: string | null
  ran_at: string
  results: CheckResult[]
}

/** Check the working circuit now (probes the ESP32 first if it's connected). */
export const runChecks = () => request<CheckReport>('POST', '/api/checks/run')

/** The report saved for a commit, or null if there isn't one. */
export async function getSavedChecks(commit = 'HEAD'): Promise<CheckReport | null> {
  try {
    return await request<CheckReport>('GET', `/api/checks?commit=${encodeURIComponent(commit)}`)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null
    throw e
  }
}

// ── Board state and branches ──────────────────────────────────────────────────────────────────

export interface RewireStep {
  step: number
  action: 'remove' | 'place' | 'move' | 'add' | 'change'
  object_type: 'wire' | 'component'
  object_id: string
  /** e.g. "Move w5: A4 (GPIO34) → A12 (GPIO12)" */
  text: string
  holes: Hole[]
  /** A move within the same strip: no electrical change. */
  optional: boolean
}

export interface BoardReport {
  branch: string
  head: string | null
  state: { matches_commit: string | null; updated_at: string | null; circuit_fingerprint: string | null }
  /** true: nothing to rewire. false: follow `guide`. null: the board was never confirmed (scan it). */
  matches: boolean | null
  assumed_from: string | null
  guide: RewireStep[]
}

export interface Branch {
  name: string
  head: string
  subject: string
  current: boolean
}

export const getBoardState = () => request<BoardReport>('GET', '/api/board-state')
export const listBranches = () => request<Branch[]>('GET', '/api/branches')
/** Creates the branch at HEAD without switching to it. */
export const createBranch = (name: string) => request<Branch>('POST', '/api/branches', { name })
/** 400 with a clear message if the circuit has uncommitted changes or scan proposals are pending. */
export const checkout = (branch: string) => request<BoardReport>('POST', '/api/checkout', { branch })

// ── Pull requests ─────────────────────────────────────────────────────────────────────────────

export interface PR {
  id: number
  title: string
  description: string
  from_branch: string
  into_branch: string
  status: 'open' | 'merged' | 'closed'
  created_at: string
  updated_at: string
  last_checked_commit: string | null
  checks: { commit: string; overall: CheckReport['status'] } | null
  tested: { done: boolean; note: string | null; commit: string | null; at: string | null }
  merged: { commit: string; into_before: string; at: string } | null
}

export interface MergeStatus {
  allowed: boolean
  reason: string
  /** needs_confirmation results: shown, but they don't block. */
  warnings: string[]
  /** not_supported results: couldn't be checked (never a pass). */
  not_checked: string[]
}

export interface PRDetail {
  pr: PR
  before: Circuit
  after: Circuit
  diff: CircuitDiff
  summary: string[]
  checks: CheckReport | null
  merge: MergeStatus
}

export interface NewPR {
  title: string
  description?: string
  /** Default: the checked-out branch. */
  from_branch?: string
  /** Default: main. */
  into_branch?: string
}

export const listPRs = () => request<PR[]>('GET', '/api/prs')
export const createPR = (pr: NewPR) => request<PRDetail>('POST', '/api/prs', pr)
/** Reruns checks first if the branch has new commits. */
export const getPR = (id: number) => request<PRDetail>('GET', `/api/prs/${id}`)
export const markTested = (id: number, note: string) => request<PR>('POST', `/api/prs/${id}/tested`, { note })
export const mergePR = (id: number) => request<PRDetail>('POST', `/api/prs/${id}/merge`)
export const closePR = (id: number) => request<{ pr: PR; guide: RewireStep[] }>('POST', `/api/prs/${id}/close`)

// ── Helpers ───────────────────────────────────────────────────────────────────────────────────

/** Holes to highlight for check-result ids: wire ends, a part's pins, or one pin ('esp32.GPIO12'). */
export function holesFor(circuit: Circuit, ids: string[]): Hole[] {
  const holes = new Set<Hole>()
  for (const id of ids) {
    const wire = circuit.wires.find((w) => w.id === id)
    if (wire) {
      holes.add(wire.a)
      holes.add(wire.b)
      continue
    }
    const [componentId, pin] = id.split('.', 2)
    const component = circuit.components.find((c) => c.id === componentId)
    if (!component) continue
    if (pin && component.pins[pin]) holes.add(component.pins[pin])
    else if (!pin) Object.values(component.pins).forEach((h) => holes.add(h))
  }
  return [...holes]
}

export const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : '—')
