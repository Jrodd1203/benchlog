# benchlog

Version control for breadboard prototypes, built by a team of 4 at a hackathon. Scan the board, review
what changed, commit it with the firmware, open a PR with automated checks.

Demo path: physical change → scan → review → visual diff → commit → timeline → push → PR → check result.
Demo story: the pot wiper wire `w5` moves from A4 (GPIO34) to A12 (GPIO12, a strapping pin that can
stop the ESP32 from booting). The check fails, the wire is fixed, the check passes. See
`examples/circuits/README.md`.

## Architecture

- Everything downstream works in **hole names** (`A12`, `R+15`), never pixels. The board layout is
  defined in code (`src/benchlog/core/board.py`), not detected.
- The camera is one source of `Observation`s; manual click-to-wire is the other. Both feed
  review → `Circuit` JSON → diff / commit / checks / PR / timeline.
- `src/benchlog/core/` must stay light (pydantic, typer, rich only). OpenCV lives in `vision/`,
  FastAPI in `server/`, so CI installs quickly.
- The same checks run locally (`benchlog check`) and in GitHub Actions.

## Ownership

| Area | Owner |
|---|---|
| `core/` (models, board, netlist, diff, repo), `cli/` | Person 1 |
| `core/checks/`, `.github/workflows/`, `firmware/` | Person 2 |
| `web/` | Person 3 |
| `vision/`, `server/` scan endpoint, `examples/photos/` | Person 4 |

Only edit your own area. If you need something from another area, write a minimal stub on your side and
tell your human what to ask the owner for.

## Shared contract

`src/benchlog/core/models.py` and the files in `examples/` are the contract between everyone.
Do not change them without your human confirming the team agreed. If they do change, regenerate the
examples so they stay canonical (`tests/test_models.py` checks this).

## Conventions

- Python 3.11+, type hints, pydantic v2. Circuit JSON is written with `core/serialize.dump`.
- Add tests in `tests/` for new logic; run `pytest` before finishing.
- Work on a branch (`<name>/<task>`), small PRs to `main`, conventional commit messages
  (`feat(core): ...`). Don't push or open PRs unless your human asks.
- macOS: if `import benchlog` fails, see the README gotcha about hidden `.pth` files.
