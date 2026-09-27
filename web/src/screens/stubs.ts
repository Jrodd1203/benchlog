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
  issues: {
    title: 'Issues',
    requirement: '11',
    features: ['Report a problem tied to a specific part and commit', 'Expected vs. observed behavior'],
    actions: [{ label: 'New issue', needs: 'an issues store (local file or GitHub Issues API)', primary: true }],
  },
} satisfies Record<string, StubSection>

export type StubId = keyof typeof STUBS
