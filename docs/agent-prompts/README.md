Kickoff prompts for each person's Claude Code session. Shared context lives in `CLAUDE.md` at the repo
root and loads automatically.

After pulling, from the repo root:

```sh
git checkout -b <name>/<task>
claude "$(cat docs/agent-prompts/person-1-core.md)"      # or person-2-checks / person-3-frontend / person-4-vision
```
