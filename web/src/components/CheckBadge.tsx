import type { CheckStatus } from '../api/prs'

const LABEL: Record<CheckStatus, string> = {
  pass: 'pass',
  fail: 'fail',
  needs_confirmation: 'confirm',
  not_supported: 'not checked',
}

const CLASS: Record<CheckStatus, string> = { pass: 'pass', fail: 'fail', needs_confirmation: 'warn', not_supported: 'na' }

/** A check status stamp. "not checked" is never shown as a pass. */
export function CheckBadge({ status }: { status: CheckStatus }) {
  return <span className={`badge ${CLASS[status]}`}>{LABEL[status]}</span>
}
