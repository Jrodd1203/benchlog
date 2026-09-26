You are working as Person 1 (Core) on benchlog. Read CLAUDE.md, `src/benchlog/core/`, `examples/`, and
`tests/test_models.py` first. You own `src/benchlog/core/` (except `checks/`) and `src/benchlog/cli/`.
Everyone else depends on your code, so keep interfaces simple and tell me when one is ready to use.

Tasks, in order. Propose a short plan first, then do one task at a time and stop after each so I can review:

1. **Netlist** (`core/netlist.py`): `netlist(circuit) -> list[Net]`. Map each hole to its strip with the
   board template, join strips connected by wires (union-find), and attach component pins. A `Net` should
   list its strips and the component pins on it (e.g. `esp32.GPIO34`, `pot1.wiper`). Give nets a
   deterministic identity so two revisions can be compared.
2. **Diff** (`core/diff.py`): `diff(old, new)` reporting electrical changes (which component pins are
   connected to what) separately from placement-only changes (moved within the same strip). Test on the
   examples: `working` → `moved-wire` should say w5 moved A4→A12, and pot1.wiper went from GPIO34 to GPIO12.
3. **Schema export**: a `benchlog schema` command that prints `Circuit`/`Observation` JSON Schema, so
   Person 3 can generate TypeScript types.
4. **Git wrapper** (`core/repo.py`): thin subprocess wrapper around `git`: init, add, commit, log, and read a
   file at a revision (`git show <rev>:<path>`). No GitPython.
5. **CLI** (`cli/main.py`): make `init`, `status`, `diff`, `commit -m`, and `log` work for real. Decide with
   me where the circuit lives in a user's project (proposal: `benchlog/circuit.json` committed, pending
   observations in an ignored `.benchlog/`). Use Rich for readable output.

Done when `benchlog diff` shows the moved wire readably and `benchlog commit` / `log` work in a scratch repo.
Add tests for every task and keep `pytest` green.
