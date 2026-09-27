// Virtual BB830 breadboard (SVG, board-mm units). Draws a circuit and, optionally, what changed
// since `previous`: added holes pulse green, removed holes show as red ghosts. The drawing style
// (curved wires, rail tints, labels on new wires, fade between commits) comes from the first
// timelapse MVP; geometry comes from ./geometry so it matches the camera's view of the board.

import { type MouseEvent, useMemo } from 'react'
import type { Circuit, Component, Hole } from '../types'
import {
  BOARD_H,
  BOARD_W,
  BOTTOM_COLUMNS,
  columnY,
  HOLES,
  holePosition,
  PITCH,
  RAILS,
  railY,
  rowX,
  TOP_COLUMNS,
} from './geometry'
import './Breadboard.css'

const HOLE_R = 0.55
const PART_COLOR: Record<Component['type'], string> = {
  esp32_devkit_v1_30: '#3b82f6',
  resistor: '#d97706',
  potentiometer: '#b45309',
  led: '#eab308',
  capacitor_electrolytic: '#2563eb',
  capacitor_ceramic: '#0891b2',
  transistor_npn: '#374151',
  diode: '#64748b',
  i2c_module: '#16a34a',
}
const WIRE_COLOR: Record<string, string> = {
  red: '#dc2626',
  black: '#1f2937',
  yellow: '#eab308',
  green: '#16a34a',
  blue: '#2563eb',
  orange: '#ea580c',
  white: '#f8fafc',
}

interface Occupant {
  color: string
  label: string
}

/** Which holes a circuit uses, and by what. */
function occupiedHoles(circuit: Circuit): Map<Hole, Occupant> {
  const holes = new Map<Hole, Occupant>()
  for (const c of circuit.components) {
    for (const [pin, hole] of Object.entries(c.pins)) holes.set(hole, { color: PART_COLOR[c.type], label: `${c.id}.${pin}` })
  }
  for (const w of circuit.wires) {
    for (const h of [w.a, w.b]) if (!holes.has(h)) holes.set(h, { color: wireColor(w.color), label: w.label ?? w.id })
  }
  return holes
}

function wireColor(name: string | null | undefined): string {
  return (name && WIRE_COLOR[name]) || name || '#94a3b8'
}

export function Breadboard({
  circuit,
  previous,
  className,
  highlight,
  onHoleClick,
}: {
  circuit: Circuit
  /** When given, highlight what changed between `previous` and `circuit`. */
  previous?: Circuit | null
  className?: string
  /** Holes to ring in amber, e.g. ones the camera wasn't sure about. */
  highlight?: Hole[]
  /** Makes every hole clickable (e.g. "pick the right hole"). */
  onHoleClick?: (hole: Hole) => void
}) {
  const occupied = useMemo(() => occupiedHoles(circuit), [circuit])
  const { added, removed } = useMemo(() => {
    if (!previous) return { added: new Set<Hole>(), removed: new Set<Hole>() }
    const before = occupiedHoles(previous)
    return {
      added: new Set([...occupied.keys()].filter((h) => !before.has(h))),
      removed: new Set([...before.keys()].filter((h) => !occupied.has(h))),
    }
  }, [occupied, previous])

  // Ghost wire paths: wires from `previous` whose endpoints changed or that no longer exist.
  const ghostWires = useMemo(() => {
    if (!previous) return []
    return previous.wires.filter((pw) => {
      const cw = circuit.wires.find((w) => w.id === pw.id)
      return !cw || cw.a !== pw.a || cw.b !== pw.b
    })
  }, [previous, circuit.wires])

  const channelY = (columnY('E') + columnY('F')) / 2
  const labelRows = [1, ...Array.from({ length: 12 }, (_, i) => (i + 1) * 5)]

  // One handler for all holes: each hole element carries data-hole.
  const handleClick = onHoleClick
    ? (e: MouseEvent<SVGSVGElement>) => {
        const hole = (e.target as Element).closest('[data-hole]')?.getAttribute('data-hole')
        if (hole) onHoleClick(hole)
      }
    : undefined

  return (
    <svg
      onClick={handleClick}
      className={`breadboard${onHoleClick ? ' pickable' : ''}${className ? ` ${className}` : ''}`}
      viewBox={`-6 -2 ${BOARD_W + 8} ${BOARD_H + 4}`}
      role="img"
      aria-label="Virtual BB830 breadboard"
    >
      {/* body, rails, centre channel */}
      <rect className="bb-body" x={0} y={0} width={BOARD_W} height={BOARD_H} rx={1.5} />
      {/* printed rail lines, as on the real BB830: red above the + row, black below the - row */}
      {(['L', 'R'] as const).map((side) => (
        <g key={side}>
          <line className="bb-rail-line plus" x1={4} x2={BOARD_W - 4} y1={railY(`${side}+`) - 1.55} y2={railY(`${side}+`) - 1.55} />
          <line className="bb-rail-line minus" x1={4} x2={BOARD_W - 4} y1={railY(`${side}-`) + 1.55} y2={railY(`${side}-`) + 1.55} />
        </g>
      ))}
      <rect className="bb-channel" x={2} y={channelY - 1.4} width={BOARD_W - 4} height={2.8} rx={0.6} />

      {/* labels: rows every 5, columns A-J, rail polarity */}
      {labelRows.map((r) => (
        <g key={r} className="bb-label">
          <text x={rowX(r)} y={columnY('A') - 2.1}>{r}</text>
          <text x={rowX(r)} y={columnY('J') + 3.1}>{r}</text>
        </g>
      ))}
      {[...TOP_COLUMNS, ...BOTTOM_COLUMNS].map((c) => (
        <text key={c} className="bb-label" x={BOARD_W + 1.2} y={columnY(c) + 0.6}>
          {c}
        </text>
      ))}
      {RAILS.map((r) => (
        <text key={r} className={`bb-label rail ${r.endsWith('+') ? 'plus' : 'minus'}`} x={-1.8} y={railY(r) + 0.7}>
          {r.endsWith('+') ? '+' : '−'}
        </text>
      ))}

      {/* key={...} remounts the drawing on every change so the fade replays */}
      <g key={circuitKey(circuit)} className="bb-fade">
        {/* empty holes */}
        {[...HOLES].map(([name, p]) =>
          occupied.has(name) || removed.has(name) ? null : (
            <rect key={name} data-hole={name} className="bb-hole" x={p.x - HOLE_R} y={p.y - HOLE_R} width={HOLE_R * 2} height={HOLE_R * 2} rx={0.15}>
              <title>{name}</title>
            </rect>
          ),
        )}

        {/* part bodies */}
        {circuit.components.map((c) => (
          <PartBody key={c.id} part={c} />
        ))}

        {/* ghost wire paths: dashed red traces showing where moved wires came from */}
        {ghostWires.map((w) => {
          const a = holePosition(w.a)
          const b = holePosition(w.b)
          if (!a || !b) return null
          const mx = (a.x + b.x) / 2
          const my = (a.y + b.y) / 2 - Math.max(Math.abs(a.x - b.x) * 0.12, 1.2)
          const d = `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`
          return <path key={`ghost-${w.id}`} className="bb-wire-ghost" d={d} />
        })}

        {/* removed: red ghosts */}
        {[...removed].map((h) => {
          const p = holePosition(h)
          return p ? (
            <g key={`rm-${h}`}>
              <circle className="bb-ring removed" cx={p.x} cy={p.y} r={HOLE_R + 0.9} />
              <circle className="bb-ghost" cx={p.x} cy={p.y} r={HOLE_R} />
            </g>
          ) : null
        })}

        {/* wires */}
        {circuit.wires.map((w) => {
          const a = holePosition(w.a)
          const b = holePosition(w.b)
          if (!a || !b) return null
          const mx = (a.x + b.x) / 2
          const my = (a.y + b.y) / 2 - Math.max(Math.abs(a.x - b.x) * 0.12, 1.2)
          const d = `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`
          const isNew = added.has(w.a) || added.has(w.b)
          const label = w.label ?? w.id
          return (
            <g key={w.id}>
              <path className="bb-wire-edge" d={d} />
              <path className="bb-wire" d={d} stroke={wireColor(w.color)}>
                <title>{`${w.id}: ${w.a} → ${w.b}${w.label ? ` (${w.label})` : ''}`}</title>
              </path>
              {isNew && (
                <g className="bb-wire-label" transform={`translate(${mx} ${my - 1.6})`}>
                  <rect x={-label.length * 0.55 - 0.6} y={-1.5} width={label.length * 1.1 + 1.2} height={2.2} rx={0.4} />
                  <text y={0.15}>{label}</text>
                </g>
              )}
            </g>
          )
        })}

        {/* occupied holes */}
        {[...occupied].map(([h, info]) => {
          const p = holePosition(h)
          if (!p) return null
          return (
            <g key={h}>
              {added.has(h) && <circle className="bb-ring added" cx={p.x} cy={p.y} r={HOLE_R + 0.9} />}
              <circle data-hole={h} className="bb-pin" cx={p.x} cy={p.y} r={HOLE_R} fill={info.color}>
                <title>{`${h} — ${info.label}`}</title>
              </circle>
            </g>
          )
        })}
        {highlight?.map((h) => {
          const p = holePosition(h)
          return p ? <circle key={`hl-${h}`} className="bb-ring highlight" cx={p.x} cy={p.y} r={HOLE_R + 1.1} /> : null
        })}
      </g>
    </svg>
  )
}

/** Simple body shapes drawn under the pins. */
function PartBody({ part }: { part: Component }) {
  const pts = Object.values(part.pins)
    .map(holePosition)
    .filter((p) => p !== undefined)
  if (pts.length === 0) return null
  const xs = pts.map((p) => p.x)
  const ys = pts.map((p) => p.y)
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  const label = part.value ? `${part.id} ${part.value}` : part.id

  if (part.type === 'esp32_devkit_v1_30') {
    return (
      <g className="bb-part esp32">
        <rect x={x0 - PITCH * 0.6} y={y0 - PITCH * 0.6} width={x1 - x0 + PITCH * 1.2} height={y1 - y0 + PITCH * 1.2} rx={0.8} />
        <text x={(x0 + x1) / 2} y={(y0 + y1) / 2 + 0.8}>ESP32 DevKit</text>
      </g>
    )
  }
  return (
    <g className="bb-part">
      <line x1={x0} y1={y0} x2={x1} y2={y1} stroke={PART_COLOR[part.type]} />
      <text x={(x0 + x1) / 2} y={Math.min(y0, y1) - 1.2}>
        {label}
      </text>
    </g>
  )
}

function circuitKey(c: Circuit): string {
  return JSON.stringify([c.components.map((p) => [p.id, p.pins]), c.wires.map((w) => [w.id, w.a, w.b])])
}
