// Mirrors the shared models in src/benchlog/core (see `benchlog schema`).
// Keep in sync by hand; field names and enums must match the JSON schema exactly.

/** A hole name such as "A12" or "R+15". */
export type Hole = string

export type ComponentType = 'esp32_devkit_v1_30' | 'resistor' | 'led' | 'potentiometer'

export interface Component {
  id: string
  type: ComponentType
  model?: string | null
  value?: string | null
  /** Pin name -> hole it is inserted in. */
  pins: Record<string, Hole>
}

export interface Wire {
  id: string
  a: Hole
  b: Hole
  color?: string | null
  label?: string | null
}

export interface Circuit {
  schema_version?: 1
  board: string
  components: Component[]
  wires: Wire[]
}

export type ObservationKind = 'added' | 'removed' | 'moved'
export type ObservationStatus = 'pending' | 'accepted' | 'rejected'

export interface Observation {
  id: string
  kind: ObservationKind
  object_type: 'wire' | 'component'
  object_id: string
  before?: Record<string, Hole> | null
  after?: Record<string, Hole> | null
  confidence: number
  uncertain_holes: Hole[]
  status: ObservationStatus
}

export interface ConnectionChange {
  kind: string
  a: string
  b: string
}

export interface PlacementChange {
  kind: string
  object_type: string
  object_id: string
  before?: Record<string, Hole> | null
  after?: Record<string, Hole> | null
  changed_ends: string[]
  within_strip: boolean
  changed_fields: string[]
}

export interface CircuitDiff {
  connections: ConnectionChange[]
  placement: PlacementChange[]
}

// ── UI-only types (no backend model yet) ─────────────────────────────────────

export interface ProjectSummary {
  id: string
  name: string
  board: string
  /** ISO date of the last commit, if any. */
  updated: string | null
  setupComplete: boolean
}

export type SerialState = 'connected' | 'disconnected' | 'unknown'

/** One commit on the timeline, oldest first. */
export interface TimelineEntry {
  sha: string
  shortSha: string
  message: string
  author: string
  /** ISO 8601 */
  date: string
  /** What this commit changed, one line per change (from GET /api/history). */
  lines: string[]
  /** True when a connection was made or broken (not just a wire moved within its strip). */
  electrical: boolean
  // Mock-only until checks and "mark as tested" exist in the backend.
  check?: 'pass' | 'fail' | 'none'
  tested?: boolean
  note?: string
}
