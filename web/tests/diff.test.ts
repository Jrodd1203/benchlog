// The browser circuit diff (src/diff.ts) against the example circuits: it must say what
// `benchlog diff` says (src/benchlog/core/diff.py). Run with: npm test

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { describeDiff, diffCircuits, naturalCompare } from '../src/diff.ts'
import { parseCheckTrailer } from '../src/checkTrailer.ts'
import type { Circuit } from '../src/types.ts'

const load = (name: string): Circuit =>
  JSON.parse(readFileSync(new URL(`../../examples/circuits/${name}.json`, import.meta.url), 'utf8')) as Circuit

test('the demo story: the pot wiper wire moves from GPIO34 to GPIO12', () => {
  const lines = describeDiff(diffCircuits(load('working'), load('moved-wire')))
  assert.deepEqual(lines, [
    'esp32.GPIO12 connected to pot1.wiper',
    'esp32.GPIO34 disconnected from pot1.wiper',
    'w5 moved: b A4 → A12',
  ])
})

test('the same circuit has no changes, and a flipped wire is the same wire', () => {
  const working = load('working')
  assert.deepEqual(describeDiff(diffCircuits(working, working)), [])
  const flipped = { ...working, wires: working.wires.map((w) => ({ ...w, a: w.b, b: w.a })) }
  assert.deepEqual(describeDiff(diffCircuits(working, flipped)), [])
})

test('a move along the same strip is placement only', () => {
  const working = load('working')
  // w5's b end is in A4 (strip 4L); C4 is on the same strip.
  const along = { ...working, wires: working.wires.map((w) => (w.id === 'w5' ? { ...w, b: 'C4' } : w)) }
  const d = diffCircuits(working, along)
  assert.equal(d.connections.length, 0)
  assert.deepEqual(describeDiff(d), ['w5 moved: b A4 → C4 (same strip, no electrical change)'])
})

test('the first build is compared with an empty board', () => {
  const working = load('working')
  const lines = describeDiff(diffCircuits(load('empty'), working))
  assert.ok(lines.some((l) => l.startsWith('esp32 added')))
  assert.ok(lines.filter((l) => l.includes(' connected to ')).length > 0)
})

test('natural order', () => {
  assert.deepEqual(['w10', 'w2', 'GPIO12', 'GPIO4'].sort(naturalCompare), ['GPIO4', 'GPIO12', 'w2', 'w10'])
})

test('the ESP32 check from the commit message', () => {
  assert.deepEqual(parseCheckTrailer('add wire\n\nESP32-Check: passed'), { status: 'passed', forced: false })
  assert.deepEqual(parseCheckTrailer('oops\n\nESP32-Check: failed (forced)\n'), { status: 'failed', forced: true })
  assert.equal(parseCheckTrailer('plain commit'), undefined)
})
