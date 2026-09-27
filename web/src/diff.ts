// Circuit diff in the browser, for Explore (read-only GitHub data, no backend).
// A port of src/benchlog/core/diff.py and netlist.py: same result shape (CircuitDiff) and the
// same plain-English lines as `benchlog diff`. Keep the two in step.
//
// Self-contained (no runtime imports) so `npm test` can load it directly.

import type { Circuit, CircuitDiff, ConnectionChange, PlacementChange } from './types'

type Hole = string
type Ends = Record<string, Hole>

const PART = /(\d+)/

/** Sorts "GPIO4" before "GPIO12" and "w2" before "w10" (core/netlist.natural_key). */
export function naturalCompare(a: string, b: string): number {
  const pa = a.split(PART)
  const pb = b.split(PART)
  for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
    if (pa[i] === pb[i]) continue
    const numeric = i % 2 === 1 // split with a capture group puts the numbers at odd indices
    if (numeric) return Number(pa[i]) - Number(pb[i])
    return pa[i] < pb[i] ? -1 : 1
  }
  return pa.length - pb.length
}

/** The internally connected strip a hole is on: a row half ("21L"/"21R") or a rail ("R+"). */
function stripOf(hole: Hole): string {
  const m = /^([A-J])(-?\d+)$/.exec(hole)
  if (m) return `${m[2]}${'ABCDE'.includes(m[1]) ? 'L' : 'R'}`
  const rail = /^([LR][+-])\d+$/.exec(hole)
  return rail ? rail[1] : hole
}

/** Every pair of component pins ("r1.1|esp32.GPIO4") that sit on the same net. */
function connectedPairs(circuit: Circuit): Set<string> {
  const parent = new Map<string, string>()
  const find = (s: string): string => {
    if (!parent.has(s)) parent.set(s, s)
    let root = s
    while (parent.get(root) !== root) root = parent.get(root)!
    parent.set(s, root)
    return root
  }
  for (const w of circuit.wires) parent.set(find(stripOf(w.a)), find(stripOf(w.b)))

  const nets = new Map<string, string[]>()
  for (const c of circuit.components) {
    for (const [pin, hole] of Object.entries(c.pins)) {
      const net = find(stripOf(hole))
      nets.set(net, [...(nets.get(net) ?? []), `${c.id}.${pin}`])
    }
  }
  const pairs = new Set<string>()
  for (const pins of nets.values()) {
    pins.sort(naturalCompare)
    for (let i = 0; i < pins.length; i++) for (let j = i + 1; j < pins.length; j++) pairs.add(`${pins[i]}|${pins[j]}`)
  }
  return pairs
}

function connections(old: Circuit, next: Circuit): ConnectionChange[] {
  const before = connectedPairs(old)
  const after = connectedPairs(next)
  const changes: ConnectionChange[] = []
  for (const p of before) if (!after.has(p)) changes.push({ kind: 'disconnected', a: p.split('|')[0], b: p.split('|')[1] })
  for (const p of after) if (!before.has(p)) changes.push({ kind: 'connected', a: p.split('|')[0], b: p.split('|')[1] })
  return changes.sort((x, y) => naturalCompare(x.a, y.a) || naturalCompare(x.b, y.b) || (x.kind < y.kind ? -1 : 1))
}

const FIELDS = { wire: ['color', 'label'], component: ['type', 'model', 'value'] } as const

interface Placed {
  id: string
  ends: Ends
  fields: Record<string, unknown>
}

function samePlacement(type: 'wire' | 'component', before: Ends, after: Ends): boolean {
  // A wire plugged in the other way round is the same wire in the same place.
  if (type === 'wire') return JSON.stringify(Object.values(before).sort()) === JSON.stringify(Object.values(after).sort())
  const keys = new Set([...Object.keys(before), ...Object.keys(after)])
  return [...keys].every((k) => before[k] === after[k])
}

function placed(circuit: Circuit, type: 'wire' | 'component'): Placed[] {
  if (type === 'wire') return circuit.wires.map((w) => ({ id: w.id, ends: { a: w.a, b: w.b }, fields: { color: w.color, label: w.label } }))
  return circuit.components.map((c) => ({ id: c.id, ends: { ...c.pins }, fields: { type: c.type, model: c.model, value: c.value } }))
}

function placement(old: Circuit, next: Circuit): PlacementChange[] {
  const changes: PlacementChange[] = []
  const groups = (['component', 'wire'] as const).map((type) => [type, placed(old, type), placed(next, type)] as const)
  for (const [type, olds, news] of groups) {
    const before = new Map(olds.map((o) => [o.id, o]))
    const after = new Map(news.map((o) => [o.id, o]))
    const ids = [...new Set([...before.keys(), ...after.keys()])].sort(naturalCompare)
    for (const id of ids) {
      const b = before.get(id)
      const a = after.get(id)
      const base = { object_type: type, object_id: id, changed_ends: [] as string[], within_strip: false, changed_fields: [] as string[] }
      if (!a) { changes.push({ ...base, kind: 'removed', before: b!.ends }); continue }
      if (!b) { changes.push({ ...base, kind: 'added', after: a.ends }); continue }
      const fields = FIELDS[type].filter((f) => (b.fields[f] ?? null) !== (a.fields[f] ?? null))
      if (!samePlacement(type, b.ends, a.ends)) {
        const ends = [...new Set([...Object.keys(b.ends), ...Object.keys(a.ends)])]
          .sort(naturalCompare)
          .filter((e) => b.ends[e] !== a.ends[e])
        const within = ends.every((e) => e in b.ends && e in a.ends && stripOf(b.ends[e]) === stripOf(a.ends[e]))
        changes.push({ ...base, kind: 'moved', before: b.ends, after: a.ends, changed_ends: ends, within_strip: within, changed_fields: [...fields] })
      } else if (fields.length) {
        changes.push({ ...base, kind: 'modified', before: b.ends, after: a.ends, changed_fields: [...fields] })
      }
    }
  }
  return changes
}

export function diffCircuits(old: Circuit, next: Circuit): CircuitDiff {
  return { connections: connections(old, next), placement: placement(old, next) }
}

/** Plain-English lines, electrical changes first (core/diff.describe). */
export function describeDiff(d: CircuitDiff): string[] {
  const lines = d.connections.map((c) => `${c.a} ${c.kind} ${c.kind === 'connected' ? 'to' : 'from'} ${c.b}`)
  for (const p of d.placement) {
    if (p.kind === 'moved') {
      const ends = p.changed_ends.map((e) => `${e} ${p.before?.[e] ?? '-'} → ${p.after?.[e] ?? '-'}`).join(', ')
      lines.push(`${p.object_id} moved: ${ends}${p.within_strip ? ' (same strip, no electrical change)' : ''}`)
    } else if (p.kind === 'added' || p.kind === 'removed') {
      lines.push(`${p.object_id} ${p.kind} (${Object.values((p.after ?? p.before)!).join(', ')})`)
    }
    if (p.changed_fields.length) lines.push(`${p.object_id} changed ${p.changed_fields.join(', ')}`)
  }
  return lines
}
