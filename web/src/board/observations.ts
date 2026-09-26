// Preview helpers for scan observations (proposed changes), used by the Review screen.

import type { Circuit, Observation } from '../types'

/** The circuit as it would look with these observations accepted (preview only; the server applies them). */
export function applyObservations(circuit: Circuit, observations: Observation[]): Circuit {
  let wires = [...circuit.wires]
  let components = [...circuit.components]
  for (const o of observations) {
    if (o.object_type === 'wire') {
      const existing = wires.find((w) => w.id === o.object_id)
      if (o.kind === 'removed') wires = wires.filter((w) => w.id !== o.object_id)
      else if (o.after?.a && o.after?.b) {
        const next = { ...(existing ?? { id: o.object_id, color: 'green' }), a: o.after.a, b: o.after.b }
        wires = existing ? wires.map((w) => (w.id === o.object_id ? next : w)) : [...wires, next]
      }
    } else {
      const existing = components.find((c) => c.id === o.object_id)
      if (o.kind === 'removed') components = components.filter((c) => c.id !== o.object_id)
      else if (existing && o.after) components = components.map((c) => (c.id === o.object_id ? { ...c, pins: o.after! } : c))
    }
  }
  return { ...circuit, wires, components }
}

/** One-line description, e.g. "Wire w5 moved: A4 → A12". */
export function describeObservation(o: Observation, circuit: Circuit): string {
  const what = o.object_type === 'wire' ? 'Wire' : 'Part'
  const label = o.object_type === 'wire' ? circuit.wires.find((w) => w.id === o.object_id)?.label : null
  const name = `${what} ${o.object_id}${label ? ` (${label})` : ''}`
  const ends = (e?: Record<string, string> | null) => (e ? Object.values(e).join('–') : '?')
  if (o.kind === 'added') return `${name} added at ${ends(o.after)}`
  if (o.kind === 'removed') return `${name} removed from ${ends(o.before)}`
  const moved = Object.keys(o.after ?? {}).filter((k) => o.before?.[k] !== o.after?.[k])
  return `${name} moved: ${moved.map((k) => `${o.before?.[k] ?? '?'} → ${o.after?.[k]}`).join(', ')}`
}

/** Holes that changed, for highlighting. */
export function observationHoles(o: Observation): string[] {
  return [...new Set([...Object.values(o.before ?? {}), ...Object.values(o.after ?? {})])]
}
