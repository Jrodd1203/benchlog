import type { RemoteCommit } from './types'

/** The `ESP32-Check: passed|failed|skipped [(forced)]` trailer `benchlog commit` adds, if present. */
export function parseCheckTrailer(message: string): RemoteCommit['check'] {
  const m = /^ESP32-Check: (passed|failed|skipped)( \(forced\))?/m.exec(message)
  return m ? { status: m[1] as 'passed' | 'failed' | 'skipped', forced: Boolean(m[2]) } : undefined
}
