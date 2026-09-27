# Branches, board state, and PRs: API reference

For the web UI. All routes are served by `benchlog serve` (port 8000; the Vite dev server proxies
`/api`). Everything is local: git branches in the project repo, PR files in `.benchlog/prs/`.
There is no GitHub and no database.

**Errors.** A request that can't be done returns `400` with `{"detail": "<what to do>"}`.
The `detail` is written for the user, so show it as-is. An unknown PR id returns `404`.

**How it fits together**

1. `POST /api/branches` creates a branch, and `POST /api/checkout` switches to it.
2. The user rewires the board if the checkout response has a `guide`, edits and commits as usual.
3. `POST /api/prs` opens a PR from the current branch into `main`.
4. `GET /api/prs/{id}` shows the before/after circuits, the diff, the checks and whether it can be
   merged. It always reads the branch's latest commit, so after each new commit fetch it again.
5. `POST /api/prs/{id}/merge` is allowed once the checks pass. Otherwise use
   `POST /api/prs/{id}/close`.

Ids in `ids`, `holes`, `object_id` and the diff are the same ids as in the circuit (`w5`, `pot1`,
`esp32.GPIO12`, hole names like `A12`), so they can be used to highlight the virtual breadboard.

---

## Board state

The physical board doesn't change when the branch does. benchlog remembers the last commit the
board was confirmed to match, and says how to rewire it when it no longer matches the checked-out
circuit.

The board is confirmed by a clean scan (no proposals), by reviewing every proposal of a scan, by
committing right after that, or by a merge.

### `GET /api/board-state`

```json
{
  "branch": "pot-gpio12",
  "head": "f0dbc1adb895579be1bf6c24f0032baca550acb4",
  "state": {
    "matches_commit": "ef0873219c68dccbdfcc035731f5bb86a41c71dc",
    "updated_at": "2026-09-26T22:17:36+00:00",
    "circuit_fingerprint": "ca260b402e120ffd"
  },
  "matches": false,
  "assumed_from": null,
  "guide": [
    {
      "step": 1,
      "action": "move",
      "object_type": "wire",
      "object_id": "w5",
      "text": "Move w5: A4 (GPIO34) → A12 (GPIO12)",
      "holes": ["A4", "A12"],
      "optional": false
    }
  ]
}
```

- `matches`: `true` means no rewiring is needed. `false` means follow `guide`. `null` means the
  board has never been confirmed, so suggest a scan.
- `guide[].action`: `remove`, `place`, `move`, `add` or `change`. Steps are already in the order to
  do them: unplug, place parts, then route wires.
- `guide[].optional`: `true` for a move within the same strip, which changes nothing electrically.
- `assumed_from`: set when the board state is unknown and the guide assumes the board still
  matches this commit (the branch you came from).

---

## Branches

### `GET /api/branches`

```json
[
  {"name": "main", "head": "ef0873219c68...", "subject": "Working circuit", "current": false},
  {"name": "pot-gpio12", "head": "f0dbc1adb895...", "subject": "Move pot to GPIO12", "current": true}
]
```

### `POST /api/branches`

Creates a branch at the current commit. It does **not** switch to it; call checkout next.

Request:
```json
{"name": "pot-gpio12"}
```
Response:
```json
{"name": "pot-gpio12", "head": "ef0873219c68...", "subject": "Working circuit", "current": false}
```
Errors (400): `branch 'pot-gpio12' already exists`, `'bad name' is not a valid branch name`,
`commit the circuit once before creating branches`.

### `POST /api/checkout`

Request:
```json
{"branch": "pot-gpio12"}
```
Response: the same shape as `GET /api/board-state`, for the new branch. If `matches` is `false`,
show the `guide` and ask the user to rewire, then scan.

Errors (400):
- `can't switch to main: the circuit has uncommitted changes. Commit them first.`
- `can't switch to main: 2 scan proposal(s) still pending. Accept or reject them first (`benchlog review`).`
- `no branch named 'x'`

---

## Pull requests

A PR only points at two branches. Its circuits always come from git: `before` is `circuit.json`
on `into_branch`, `after` is `circuit.json` at the latest commit of `from_branch`.

### The PR object

Returned by the list, tested and close routes, and as `pr` in the detail.

```json
{
  "id": 1,
  "title": "Move pot signal",
  "description": "Free up GPIO34",
  "from_branch": "pot-gpio12",
  "into_branch": "main",
  "status": "open",
  "created_at": "2026-09-26T22:17:36+00:00",
  "updated_at": "2026-09-26T22:17:36+00:00",
  "last_checked_commit": "f0dbc1adb895579be1bf6c24f0032baca550acb4",
  "checks": {"commit": "f0dbc1adb895...", "overall": "fail"},
  "tested": {"done": false, "note": null, "commit": null, "at": null},
  "merged": null
}
```

- `status`: `open`, `merged` or `closed`.
- `checks.overall`: `pass`, `fail` or `needs_confirmation`, for the commit in `checks.commit`.
- `tested.done` goes back to `false` as soon as the branch gets a new commit; the note is kept.
- `merged`: `{"commit", "into_before", "at"}` once merged.

### `GET /api/prs`

All PRs, oldest first: an array of PR objects. The list doesn't rerun checks, so `checks` can be
out of date until the PR is opened with `GET /api/prs/{id}`.

### `POST /api/prs`

Request (only `title` is required):
```json
{
  "title": "Move pot signal",
  "description": "Free up GPIO34",
  "from_branch": "pot-gpio12",
  "into_branch": "main"
}
```
- `from_branch` defaults to the checked-out branch.
- `into_branch` defaults to `main` (or `master` in repos without a `main`).

Response: the PR detail, the same as `GET /api/prs/{id}`. Checks run on the branch's latest commit
when the PR is created.

Errors (400): `a PR needs a title`, `can't open a PR from main into itself`,
`pot-gpio12 has no commits that aren't already in main`,
`there's already an open PR from pot-gpio12 into main`.

### `GET /api/prs/{id}`

Everything the PR page needs. If the branch got new commits since the last check, checks rerun
before responding, so this can take a moment.

```json
{
  "pr": { "...": "the PR object above" },
  "before": { "board": "bb830", "components": ["..."], "wires": ["..."], "schema_version": 1 },
  "after":  { "board": "bb830", "components": ["..."], "wires": ["..."], "schema_version": 1 },
  "diff": {
    "connections": [
      {"kind": "connected", "a": "esp32.GPIO12", "b": "pot1.wiper"},
      {"kind": "disconnected", "a": "esp32.GPIO34", "b": "pot1.wiper"}
    ],
    "placement": [
      {
        "kind": "moved", "object_type": "wire", "object_id": "w5",
        "before": {"a": "F21", "b": "A4"}, "after": {"a": "F21", "b": "A12"},
        "changed_ends": ["b"], "within_strip": false, "changed_fields": []
      }
    ]
  },
  "summary": [
    "esp32.GPIO12 connected to pot1.wiper",
    "esp32.GPIO34 disconnected from pot1.wiper",
    "w5 moved: b A4 → A12"
  ],
  "checks": {
    "status": "fail",
    "commit": "f0dbc1adb895579be1bf6c24f0032baca550acb4",
    "ran_at": "2026-09-26T22:17:36+00:00",
    "results": [
      {"check": "short", "status": "pass", "message": "No supply is connected to GND.", "ids": []},
      {"check": "unconfirmed_wires", "status": "pass", "message": "No wires or parts waiting for confirmation.", "ids": []},
      {"check": "serial_conflicts", "status": "not_supported", "message": "No serial results saved with this circuit. Scan with the ESP32 connected and accept the proposals.", "ids": []},
      {"check": "i2c_missing", "status": "pass", "message": "No I2C modules declared.", "ids": []},
      {
        "check": "esp32_pins", "status": "fail",
        "message": "GPIO12 held high at boot can stop the ESP32 from starting. Move the wire to a safe pin.",
        "ids": ["esp32.GPIO12", "w5", "pot1.wiper"]
      }
    ]
  },
  "merge": {
    "allowed": false,
    "reason": "checks failed: GPIO12 held high at boot can stop the ESP32 from starting. Move the wire to a safe pin.",
    "warnings": [],
    "not_checked": ["No serial results saved with this circuit. Scan with the ESP32 connected and accept the proposals."]
  }
}
```

- `before` and `after` are full circuits (the same shape as `GET /api/circuit`), ready for the two
  virtual breadboards.
- `diff` has the same shape as in `GET /api/diff`, and `summary` is the same text as the CLI prints.
- `checks.results[].status`: `pass`, `fail`, `needs_confirmation` or `not_supported` ("couldn't
  check", never a pass).
- `serial_conflicts` and `i2c_missing` read the serial results saved in the branch's
  `circuit.json` (`circuit.serial`, see below). Without saved results, or when they're from before
  the circuit was last edited, they're `not_supported`.
- `merge.allowed` drives the Merge button, and `merge.reason` says why. **Only a failing check
  blocks a merge.** Possible reasons:
  - `no checks failed and the branch can be fast-forwarded`, optionally followed by
    `; 2 warnings to confirm first`
  - `checks failed: …`
  - `main changed since this branch was created. Update your branch first.`
  - `this PR is merged` or `this PR is closed`
- `merge.warnings`: messages of `needs_confirmation` checks, e.g. a wire on strapping pin GPIO2.
  They don't block the merge. Show them prominently next to the Merge button, for example as a
  "Merge anyway" confirmation.
- `merge.not_checked`: messages of `not_supported` checks. They never block and never count as a
  pass, so show them as "not checked".
- For a merged PR, `before` and `after` stay what they were at merge time.

### `POST /api/prs/{id}/tested`

Records that the branch's latest commit was tried on the real board.

Request:
```json
{"note": "pot reads 0-3.3V on GPIO32"}
```
Response: the PR object with
`"tested": {"done": true, "note": "...", "commit": "<branch head>", "at": "..."}`.
Error (400): `PR #1 is closed` (or merged).

### `POST /api/prs/{id}/merge`

No body. A fast-forward merge: `into_branch` moves to the branch's latest commit, with no merge
commit. Afterwards the repo is on `into_branch`.

Response: the PR detail with `pr.status: "merged"`. `merge.warnings` still lists any warnings the
PR was merged with.

Errors (400): `can't merge PR #1: <merge.reason>`, or `can't merge: the circuit has uncommitted
changes…` / `…scan proposal(s) still pending…`.

### `POST /api/prs/{id}/close`

No body. Closes the PR without merging and returns how to put the board back to `into_branch`.

```json
{
  "pr": { "...": "the PR object, status closed" },
  "guide": [
    {"step": 1, "action": "move", "object_type": "wire", "object_id": "w5",
     "text": "Move w5: A12 (GPIO12) → A4 (GPIO34)", "holes": ["A12", "A4"], "optional": false}
  ]
}
```
Closing doesn't switch branches. Offer a "Switch to main" button that calls `POST /api/checkout`.

Error (400): `PR #1 is already closed`.

---

## Serial results in `circuit.json`

When scan proposals are accepted after a scan taken with the ESP32 connected, the serial verdicts
for the resulting circuit are saved in `circuit.serial` and committed with it. That's why checks
can use them on PRs and on past commits.

```json
"serial": {
  "probed_at": "2026-09-26T22:49:58.664+00:00",
  "port": "/dev/cu.usbserial-0001",
  "agent": "0.1.0",
  "wiring": "3f9a1c0b7d2e4a61",
  "pins": [
    {"gpio": 18, "expected": "pulled_low", "actual": "floating", "verdict": "conflict",
     "message": "GPIO18 should read pulled_low (tied to GND) but reads floating. A wire may have come loose; press it in and rescan."}
  ],
  "i2c": [{"address": "0x76", "component": "bme", "status": "confirmed"}]
}
```

- `wiring` is a fingerprint of the parts and wires the results were checked against. If the
  circuit is edited afterwards, the results count as out of date.
- `i2c` is `null` when the bus wasn't scanned.
- If the UI sends a circuit with `PUT /api/circuit`, pass `serial` through unchanged. Leaving it out
  drops the saved results.

## Checks (for reference)

- `POST /api/checks/run`: check the working circuit now, probing the ESP32 first if it's connected.
- `GET /api/checks?commit=<sha|HEAD>`: the report saved for a commit (404 if there isn't one).

Both return the same shape as `checks` in the PR detail.
