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

/** Each part drawn like its icon (components/ComponentIcon), sized in board millimetres, under its pins. */
function PartBody({ part }: { part: Component }) {
  const pins = Object.entries(part.pins)
    .map(([name, hole]) => [name, holePosition(hole)] as const)
    .filter((p): p is readonly [string, NonNullable<ReturnType<typeof holePosition>>] => p[1] !== undefined)
  if (pins.length === 0) return null
  const xs = pins.map(([, p]) => p.x)
  const ys = pins.map(([, p]) => p.y)
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  const label = part.value ? `${part.id} ${part.value}` : part.id
  const title = <title>{`${part.id}: ${part.type}${part.value ? ` ${part.value}` : ''}`}</title>

  if (part.type === 'esp32_devkit_v1_30') {
    // The USB connector sits at the VIN end of the module.
    const vin = part.pins.VIN ? holePosition(part.pins.VIN) : undefined
    const usbLeft = vin ? vin.x < (x0 + x1) / 2 : false
    const pad = PITCH * 0.6
    return (
      <g className="bb-part esp32">
        {title}
        <rect x={x0 - pad} y={y0 - pad} width={x1 - x0 + pad * 2} height={y1 - y0 + pad * 2} rx={0.8} />
        {vin && <rect className="usb" x={usbLeft ? x0 - pad - 2.2 : x1 + pad} y={(y0 + y1) / 2 - 2} width={2.2} height={4} rx={0.3} />}
        <text x={(x0 + x1) / 2} y={(y0 + y1) / 2 + 0.8}>ESP32 DevKit</text>
      </g>
    )
  }

  const byName = new Map(pins)
  const two = TWO_LEGGED[part.type]
  if (two && byName.has(two[0]) && byName.has(two[1])) {
    const a = byName.get(two[0])!
    const b = byName.get(two[1])!
    const length = Math.hypot(b.x - a.x, b.y - a.y)
    const angle = (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI
    return (
      <g className="bb-part">
        {title}
        <g transform={`translate(${(a.x + b.x) / 2} ${(a.y + b.y) / 2}) rotate(${angle})`}>
          <line className="bb-lead" x1={-length / 2} y1={0} x2={length / 2} y2={0} stroke={LEAD_COLOR[part.type]} />
          <AxialBody type={part.type} length={length} value={part.value} />
        </g>
        <text x={(a.x + b.x) / 2} y={Math.min(a.y, b.y) - 2.4}>{label}</text>
      </g>
    )
  }

  // Three or more legs (transistor, pot, I2C module): a body at the centre, a lead to each pin.
  const cx = xs.reduce((t, x) => t + x, 0) / xs.length
  const cy = ys.reduce((t, y) => t + y, 0) / ys.length
  const spanX = Math.max(x1 - x0, PITCH)
  return (
    <g className="bb-part">
      {title}
      {pins.map(([name, p]) => (
        <line key={name} className="bb-lead" x1={cx} y1={cy} x2={p.x} y2={p.y} stroke={LEAD_COLOR[part.type] ?? '#374151'} />
      ))}
      {part.type === 'potentiometer' ? (
        <g>
          <rect x={cx - spanX / 2 - 1.4} y={cy - 2.6} width={spanX + 2.8} height={5.2} rx={0.5} fill="#6b7280" stroke="#374151" strokeWidth={0.2} />
          <circle cx={cx} cy={cy} r={2.1} fill="#374151" />
          <line x1={cx} y1={cy} x2={cx} y2={cy - 1.8} stroke="#9ca3af" strokeWidth={0.35} />
        </g>
      ) : part.type === 'i2c_module' ? (
        <g>
          <rect x={cx - spanX / 2 - 1.2} y={cy - 3.2} width={spanX + 2.4} height={6.4} rx={0.5} fill="#1f7a4d" stroke="#0f4d2f" strokeWidth={0.2} />
          <rect x={cx - 1.8} y={cy - 1.3} width={3.6} height={2.6} fill="#1f2328" />
        </g>
      ) : (
        // transistor (TO-92): a flat-sided black body
        <path d={`M ${cx - spanX / 2 - 0.6} ${cy + 1} L ${cx - spanX / 2 - 0.6} ${cy - 0.2} A ${spanX / 2 + 0.6} ${2.2} 0 0 1 ${cx + spanX / 2 + 0.6} ${cy - 0.2} L ${cx + spanX / 2 + 0.6} ${cy + 1} Z`} fill="#1f2328" stroke="#000" strokeWidth={0.15} />
      )}
      <text x={cx} y={Math.min(y0, cy - 3.4) - 1}>{label}</text>
    </g>
  )
}

/** Two-legged parts: their pins in order, first leg to second (core/parts.TWO_LEGS). */
const TWO_LEGGED: Partial<Record<Component['type'], [string, string]>> = {
  resistor: ['1', '2'],
  diode: ['anode', 'cathode'],
  capacitor_ceramic: ['1', '2'],
  led: ['anode', 'cathode'],
  capacitor_electrolytic: ['+', '-'],
}

const LEAD_COLOR: Partial<Record<Component['type'], string>> = {
  resistor: '#b7791f',
  diode: '#64748b',
  capacitor_ceramic: '#0891b2',
  led: '#6b7280',
  capacitor_electrolytic: '#6b7280',
  transistor_npn: '#4b5563',
  potentiometer: '#4b5563',
  i2c_module: '#0f4d2f',
}

const LED_COLORS: Record<string, { fill: string; edge: string; shine: string }> = {
  red: { fill: '#ef4444', edge: '#7f1d1d', shine: '#fecaca' },
  green: { fill: '#22c55e', edge: '#14532d', shine: '#bbf7d0' },
  blue: { fill: '#3b82f6', edge: '#1e3a8a', shine: '#bfdbfe' },
  yellow: { fill: '#facc15', edge: '#854d0e', shine: '#fef9c3' },
  orange: { fill: '#f97316', edge: '#7c2d12', shine: '#fed7aa' },
  amber: { fill: '#f59e0b', edge: '#78350f', shine: '#fde68a' },
  white: { fill: '#f8fafc', edge: '#64748b', shine: '#ffffff' },
  purple: { fill: '#a855f7', edge: '#581c87', shine: '#e9d5ff' },
  pink: { fill: '#ec4899', edge: '#831843', shine: '#fbcfe8' },
  rgb: { fill: '#e5e7eb', edge: '#6b7280', shine: '#ffffff' },
}
LED_COLORS.violet = LED_COLORS.purple
LED_COLORS.uv = LED_COLORS.purple

/** An LED's colour from its value ("green", "5mm blue", "Warm white"...); red when it doesn't say. */
function ledColor(value: string | null | undefined): { fill: string; edge: string; shine: string } {
  const words = (value ?? '').toLowerCase().split(/[^a-z]+/)
  const name = words.find((w) => w in LED_COLORS)
  return LED_COLORS[name ?? 'red']
}

/** A two-legged part's body, centred on the origin with its legs along x (first leg at -x). */
function AxialBody({ type, length, value }: { type: Component['type']; length: number; value?: string | null }) {
  switch (type) {
    case 'resistor': {
      const w = Math.min(Math.max(length - 1.6, 3.4), 6.3)
      return (
        <g>
          <rect x={-w / 2} y={-1.15} width={w} height={2.3} rx={0.6} fill="#e8d4a0" stroke="#a3862f" strokeWidth={0.15} />
          {[-0.3, -0.1, 0.1].map((f, i) => (
            <rect key={i} x={f * w - 0.25} y={-1.15} width={0.5} height={2.3} fill={['#7a4a1e', '#b91c1c', '#7a4a1e'][i]} />
          ))}
          <rect x={0.3 * w - 0.25} y={-1.15} width={0.5} height={2.3} fill="#c8a24a" />
        </g>
      )
    }
    case 'diode': {
      const w = Math.min(Math.max(length - 1.6, 3.2), 5.2)
      return (
        <g>
          <rect x={-w / 2} y={-1} width={w} height={2} rx={0.3} fill="#26282c" />
          {/* the band marks the cathode (second leg) */}
          <rect x={w / 2 - 1.1} y={-1} width={0.55} height={2} fill="#e5e7eb" />
        </g>
      )
    }
    case 'capacitor_ceramic':
      return <ellipse cx={0} cy={0} rx={2} ry={1.6} fill="#cbd5c9" stroke="#5b6754" strokeWidth={0.15} />
    case 'led': {
      // Seen from above: the round dome in the LED's colour, flat on the cathode side.
      const { fill, edge, shine } = ledColor(value)
      return (
        <g>
          <circle cx={0} cy={0} r={2.5} fill={fill} stroke={edge} strokeWidth={0.2} opacity={0.92} />
          <circle cx={-0.8} cy={-0.8} r={0.7} fill={shine} opacity={0.7} />
          <line x1={2.2} y1={-1.2} x2={2.2} y2={1.2} stroke={edge} strokeWidth={0.25} />
        </g>
      )
    }
    case 'capacitor_electrolytic':
      // Seen from above: the can, with the stripe on the - side.
      return (
        <g>
          <circle cx={0} cy={0} r={2.6} fill="#2b5fa8" stroke="#173a6b" strokeWidth={0.2} />
          <path d="M 1.2 -2.3 A 2.6 2.6 0 0 1 1.2 2.3 Z" fill="#e5e7eb" opacity={0.85} />
        </g>
      )
    default:
      return null
  }
}

function circuitKey(c: Circuit): string {
  return JSON.stringify([c.components.map((p) => [p.id, p.pins]), c.wires.map((w) => [w.id, w.a, w.b])])
}
