// Content for skeleton screens that aren't built yet. Numbers match the team's Notion list.

export interface StubSection {
  title: string
  requirement: string
  /** What the screen will show, from the team's requirements list. */
  features: string[]
  /** Actions, each with what it needs before it can work. */
  actions: { label: string; needs: string; primary?: boolean }[]
}

export const STUBS = {
  checks: {
    title: 'Checks',
    requirement: '9',
    features: [
      'Power-to-ground short',
      'Unconfirmed wires',
      'Camera vs. serial conflicts',
      'I2C sensor not responding',
      'Results: pass / fail / needs confirmation',
    ],
    actions: [{ label: 'Run checks', needs: '`benchlog check` exposed as an API endpoint (Person 2)', primary: true }],
  },
  github: {
    title: 'GitHub: branches and PRs',
    requirement: '10',
    features: [
      'Create a branch to try a change without breaking main',
      'Open a PR with a before/after virtual breadboard for the two branches',
      'Checks run automatically on the PR (GitHub Actions)',
      'Merge button, enabled only when checks pass',
    ],
    actions: [
      { label: 'Connect GitHub', needs: 'GitHub auth (gh CLI or OAuth) on the backend' },
      { label: 'New branch', needs: 'a branch endpoint (core/repo.py)' },
      { label: 'Push', needs: 'a push endpoint + GitHub auth' },
      { label: 'Open PR', needs: 'GitHub API: create pull request', primary: true },
      { label: 'Merge', needs: 'GitHub API: merge, only when PR checks pass' },
    ],
  },
  issues: {
    title: 'Issues',
    requirement: '11',
    features: ['Report a problem tied to a specific part and commit', 'Expected vs. observed behavior'],
    actions: [{ label: 'New issue', needs: 'an issues store (local file or GitHub Issues API)', primary: true }],
  },
} satisfies Record<string, StubSection>

export type StubId = keyof typeof STUBS
