// BB830 hole geometry in board millimetres. Ported from src/benchlog/vision/bb830_layout.py
// (measured from a top-down photo) so the virtual board matches what the camera sees.
//
// Frame: origin = top-left of the board body, x right, y down, 165.1 x 54.6 mm, landscape.
// Rows 1..63 run horizontally with row 1 at the RIGHT. Columns A-E are the top half, F-J the
// bottom half. Rail pairs: L = top (next to A), R = bottom (next to J); "+" row above "-" in both.
// Rail index 1 is the end nearest row 1 (right side), increasing leftward.

import type { Hole } from '../types'

export const BOARD_W = 165.1
export const BOARD_H = 54.6
export const PITCH = 2.54

const TERM_X0 = 5.02 // x of row 63 (leftmost)
const TERM_Y0 = 14.06 // y of column A
const CHANNEL_GAP = 7.6 // E -> F centre channel
const RAIL_TOP_PLUS_Y = 4.54
const RAIL_BOTTOM_PLUS_Y = 49.22
const RAIL_TOP_X1 = 156.68
const RAIL_BOTTOM_X1 = 157.66
const RAIL_GROUP_GAP = 2 * PITCH

export const ROWS = 63
export const TOP_COLUMNS = 'ABCDE'
export const BOTTOM_COLUMNS = 'FGHIJ'
export const RAILS = ['L+', 'L-', 'R+', 'R-'] as const
export const RAIL_LENGTH = 50

export interface Point {
  x: number
  y: number
}

export function rowX(row: number): number {
  return TERM_X0 + (ROWS - row) * PITCH
}

export function columnY(col: string): number {
  const top = TOP_COLUMNS.indexOf(col)
  if (top >= 0) return TERM_Y0 + top * PITCH
  return TERM_Y0 + 4 * PITCH + CHANNEL_GAP + BOTTOM_COLUMNS.indexOf(col) * PITCH
}

function railX(idx: number, x1: number): number {
  const group = Math.floor((idx - 1) / 5)
  const pos = (idx - 1) % 5
  return x1 - (group * (4 * PITCH + RAIL_GROUP_GAP) + pos * PITCH)
}

export function railY(rail: (typeof RAILS)[number]): number {
  switch (rail) {
    case 'L+':
      return RAIL_TOP_PLUS_Y
    case 'L-':
      return RAIL_TOP_PLUS_Y + PITCH
    case 'R+':
      return RAIL_BOTTOM_PLUS_Y
    case 'R-':
      return RAIL_BOTTOM_PLUS_Y + PITCH
  }
}

function build(): Map<Hole, Point> {
  const m = new Map<Hole, Point>()
  for (let row = 1; row <= ROWS; row++) {
    for (const col of TOP_COLUMNS + BOTTOM_COLUMNS) m.set(`${col}${row}`, { x: rowX(row), y: columnY(col) })
  }
  for (const rail of RAILS) {
    const x1 = rail.startsWith('L') ? RAIL_TOP_X1 : RAIL_BOTTOM_X1
    for (let i = 1; i <= RAIL_LENGTH; i++) m.set(`${rail}${i}`, { x: railX(i, x1), y: railY(rail) })
  }
  return m
}

/** Every one of the 830 holes, name -> centre in board mm. */
export const HOLES: ReadonlyMap<Hole, Point> = build()

export function holePosition(hole: Hole): Point | undefined {
  return HOLES.get(hole)
}

/** Holes that are connected inside the board: same row + same half, or the same rail. */
export function stripOf(hole: Hole): string {
  const m = /^([A-J])(\d+)$/.exec(hole)
  if (m) return `${m[2]}${TOP_COLUMNS.includes(m[1]) ? 'top' : 'bottom'}`
  return hole.slice(0, 2)
}
