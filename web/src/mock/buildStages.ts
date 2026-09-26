// MOCK build history for the timeline slider, built from examples/circuits/working.json.
// Replace with GET /api/history + GET /api/circuit/{sha} once the timeline is wired up.
//
// Tells the demo story from CLAUDE.md: the circuit is built up step by step, then the pot wiper
// wire w5 moves from A4 (GPIO34) to A12 (GPIO12, a strapping pin), the check fails, and it's fixed.

import working from '../../../examples/circuits/working.json'
import type { Circuit, Component, Wire } from '../types'

export type CheckResult = 'pass' | 'fail' | 'none'

export interface BuildStage {
  sha: string
  message: string
  author: string
  /** ISO timestamp */
  date: string
  circuit: Circuit
  /** Marked as physically tested on the bench. */
  tested: boolean
  check: CheckResult
  /** Short note shown under the commit, e.g. why a check failed. */
  note?: string
}

const full = working as unknown as Circuit
const part = (id: string): Component => full.components.find((c) => c.id === id)!
const wire = (id: string, patch: Partial<Wire> = {}): Wire => ({ ...full.wires.find((w) => w.id === id)!, ...patch })

function circuit(components: string[], wires: Wire[]): Circuit {
  return { board: 'bb830', components: components.map(part), wires }
}

const base = ['w1', 'w2', 'w3', 'w4'].map((id) => wire(id))

export const BUILD_STAGES: BuildStage[] = [
  {
    sha: 'a1c9e02',
    message: 'Baseline: empty BB830',
    author: 'ethan',
    date: '2026-09-26T13:02:00',
    circuit: circuit([], []),
    tested: false,
    check: 'none',
  },
  {
    sha: 'b47f3d1',
    message: 'Seat ESP32 DevKit in rows 1–15',
    author: 'ethan',
    date: '2026-09-26T13:20:00',
    circuit: circuit(['esp32'], []),
    tested: false,
    check: 'pass',
  },
  {
    sha: 'c0e8a55',
    message: 'Power rails: 3V3 and GND',
    author: 'jaden',
    date: '2026-09-26T13:41:00',
    circuit: circuit(['esp32'], [wire('w1'), wire('w2')]),
    tested: true,
    check: 'pass',
  },
  {
    sha: 'd93b27e',
    message: 'Add 10kΩ pot across the rails',
    author: 'tomas',
    date: '2026-09-26T14:05:00',
    circuit: circuit(['esp32', 'pot1'], base),
    tested: false,
    check: 'pass',
  },
  {
    sha: 'e5a1f90',
    message: 'Pot wiper to GPIO34',
    author: 'tomas',
    date: '2026-09-26T14:18:00',
    circuit: circuit(['esp32', 'pot1'], [...base, wire('w5')]),
    tested: true,
    check: 'pass',
  },
  {
    sha: 'f2d6c43',
    message: 'LED on GPIO13 through 220Ω',
    author: 'ethan',
    date: '2026-09-26T14:52:00',
    circuit: circuit(['esp32', 'pot1', 'r1', 'led1'], [...base, wire('w5'), wire('w6')]),
    tested: true,
    check: 'pass',
  },
  {
    sha: '0a7be19',
    message: 'Move pot signal to GPIO12',
    author: 'jaden',
    date: '2026-09-26T15:30:00',
    circuit: circuit(['esp32', 'pot1', 'r1', 'led1'], [...base, wire('w5', { b: 'A12' }), wire('w6')]),
    tested: false,
    check: 'fail',
    note: 'GPIO12 is a strapping pin: the pot can hold it high at boot and the ESP32 won’t start.',
  },
  {
    sha: '1b3e8d7',
    message: 'Revert pot signal to GPIO34',
    author: 'jaden',
    date: '2026-09-26T15:44:00',
    circuit: circuit(['esp32', 'pot1', 'r1', 'led1'], [...base, wire('w5'), wire('w6')]),
    tested: true,
    check: 'pass',
  },
]
