# API endpoints for UI

What the web app (`web/`, branch `ethan/web-ui`) needs from the backend, by Notion requirement. Existing
endpoints are listed so everyone sees the whole contract; **new** ones are what we still need to build.
Shapes are JSON; field names follow the existing pydantic models (`Circuit`, `Observation`, `Commit`, ...).

## Decisions (agreed)

1. **Active project.** The server keeps a list of projects and one *active* project. Every existing
   endpoint keeps working unchanged and acts on the active one.
2. **Camera stays on the backend.** OpenCV owns the camera; the UI shows an MJPEG stream, only during
   calibration. Scans and calibration use the same camera.
3. **Local first, GitHub later.** Branches, PRs, issues and "tested" all live in the git repo and work
   offline. Git hosting goes behind one small provider interface (`LocalProvider` now,
   `GitHubProvider` post-MVP) so the endpoints and UI don't change when GitHub is added.
4. **Errors:** non-2xx with `{"detail": "plain-English message"}` (FastAPI default). The UI shows `detail`.

---

## Already built (UI just wires these up)

| Req | Endpoint | Notes |
|---|---|---|
| 3 | `GET /api/circuit` · `PUT /api/circuit` | Working circuit. Setup (board type, ESP32 position, parts) saves through `PUT` too. |
| 3 | `POST /api/scan` | On `main`: returns `observations`, `serial` snapshot and `reconciliation` (verdicts). |
| 3, 5 | `GET /api/serial/status` · `GET /api/serial/ports` · `POST /api/serial/connect` · `POST /api/serial/probe` | |
| 4 | `GET /api/observations` · `POST /api/observations/accept` · `POST /api/observations/reject` · `PATCH /api/observations/{id}` | `PATCH` body `{"ends": {"b": "J45"}}` = "pick the right hole". |
| 6 | `GET /api/status` · `POST /api/commit` | |
| 7, 8 | `GET /api/history` · `GET /api/circuit/{rev}` · `GET /api/diff?old=&new=` · `GET /api/netlist` | `history` is newest first. |

---

## New — core (reqs 1–7), needed for the demo

### 1. Projects — Person 1

Project list stored at `~/.benchlog/projects.json` so it survives restarts.

```
GET  /api/projects
  → [{ "id": "a1b2", "name": "ESP32 pot demo", "path": "C:/.../pot-demo", "board": "bb830",
       "active": true, "setup_complete": true, "last_commit": Commit | null }]

POST /api/projects                      create a folder + git init + benchlog init
  { "name": "ESP32 pot demo", "path": "C:/.../pot-demo", "board": "bb830" }
  → Project  (409 if the folder is already a project)

POST /api/projects/open                 register an existing benchlog folder
  { "path": "C:/.../pot-demo" }
  → Project  (400 if it isn't a benchlog project)

POST /api/projects/{id}/activate
  → Project  (every other endpoint now uses this project)
```

`setup_complete` = calibration saved and baseline taken (lets the UI send new projects to Setup).

### 2. Setup: camera, calibration, baseline — Person 4

```
GET  /api/camera/list
  → [{ "index": 0, "name": "FaceTime HD Camera", "width": 1920, "height": 1080 }]

PUT  /api/camera                        same as `benchlog camera use`
  { "index": 1 }  → { "index": 1 }

GET  /api/camera/stream                 multipart/x-mixed-replace MJPEG, ~10 fps
  The UI only opens this on the calibration step and closes it when leaving,
  so the backend can release the camera when no client is connected.

GET  /api/camera/frame                  one JPEG (fallback if streaming is flaky)

POST /api/calibration                   wraps vision.calibration.fit_holes
  { "corners": [[x, y], [x, y], [x, y], [x, y]] }   // TL, TR, BR, BL in stream pixels
  → { "holes": { "A1": [x, y], ... },               // overlay the UI draws on the stream
      "residual_px": 0.9, "ok": true, "warnings": [] }

GET  /api/calibration
  → same shape as above, or 404 if not calibrated yet

POST /api/baseline                      capture + analyse the empty board, save as baseline
  → { "present": true, "empty": true, "occupied": [], "residual_px": 0.9,
      "warnings": [], "image_url": "/api/baseline/image" }

GET  /api/baseline/image                the saved baseline JPEG (for the preview)
```

### 4. Review extras

```
GET /api/observations                   (existing) — ADD the latest reconciliation verdict
  → [{ ...Observation, "verdict": "confirmed" | "conflict" | "no_expectation" | "not_checked",
       "verdict_message": "..." | null }]
  Today verdicts only come back in the /api/scan response, so they're lost on reload.
  Owner: Person 1 + Tomas (store the last Reconciliation with the observations).

GET /api/observations/{id}/photo        JPEG crop of the scan around the changed holes — Person 4
```

UI mapping: `confirmed` → "confirmed", `conflict` → "conflict", `no_expectation`/`not_checked` → "camera only".

### 5. Serial — Tomas

```
POST /api/serial/disconnect  → SerialStatus        (nice to have)
```

### 6. Staging and "mark as tested" — Person 1

```
POST /api/stage     { "what": ["circuit"] | ["firmware"] | ["circuit", "firmware"] }  → StatusResponse
POST /api/unstage   { "what": [...] }                                                   → StatusResponse
  StatusResponse gains: "staged": { "circuit": bool, "firmware": ["firmware/main.cpp", ...] }

POST /api/commits/{sha}/tested          stored as a git note on refs/notes/benchlog-tested
  { "note": "LED blinks, pot reads 0-4095" }  → { "sha": "...", "tested": true, "note": "..." }
DELETE /api/commits/{sha}/tested        → { "sha": "...", "tested": false }
```

### 7. Timeline

```
GET /api/history                        (existing) — ADD per entry:
  "tested": bool, "tested_note": str | null, "checks": "pass" | "fail" | "needs_confirmation" | null
```

---

## New — next (reqs 8–11)

### 9. Checks — Person 2

```
GET /api/checks?rev=HEAD                (omit rev = working circuit)
  → { "overall": "pass" | "fail" | "needs_confirmation",
      "results": [{ "id": "power-short", "title": "No power-to-ground short",
                    "status": "pass" | "fail" | "needs_confirmation" | "unsupported",
                    "message": "...", "involved": ["w2", "esp32.GND1"] }] }
```

Checks to cover: power-to-ground short, unconfirmed wires, camera vs. serial conflicts, I2C sensor not
responding. `involved` = ids/holes the UI highlights on the board.

### 10. Branches and PRs (local now, GitHub later) — Person 1

```
GET  /api/branches        → [{ "name": "main", "current": true, "head": Commit }]
POST /api/branches        { "name": "try-gpio12", "from": "main" } → Branch
POST /api/checkout        { "branch": "try-gpio12" } → StatusResponse   (409 if uncommitted changes)

POST /api/prs             { "from": "try-gpio12", "to": "main", "title": "...", "body": "..." }
  → PR   stored locally in .benchlog/prs/<n>.json
GET  /api/prs             → [PR]
GET  /api/prs/{n}
  → { "number": 1, "from": "try-gpio12", "to": "main", "title": "...", "state": "open" | "merged",
      "before": Circuit, "after": Circuit, "diff": DiffResponse,
      "checks": <GET /api/checks result for the head of `from`>, "mergeable": bool }
POST /api/prs/{n}/merge   → PR   (409 unless checks pass; local `git merge`)

POST /api/push            → 501 {"detail": "GitHub not configured"} until GitHubProvider exists
```

Provider interface (backend only): `list_prs / create_pr / get_pr / merge_pr / push`. `LocalProvider`
implements them with git + `.benchlog/prs/`; `GitHubProvider` later maps them to the GitHub API
(PR numbers, checks from Actions) with no endpoint changes.

### 11. Issues — Person 1

Stored in the repo as `.benchlog/issues/<n>.json`, so they're committed with the project.

```
GET  /api/issues          → [Issue]
POST /api/issues
  { "title": "Pot reading jumps", "component": "pot1", "commit": "0a7be19",
    "expected": "0–4095 smooth", "observed": "stuck at 4095 after boot" }
  → Issue  { "number": 1, ..., "status": "open", "created": "ISO date" }
PATCH /api/issues/{n}     { "status": "closed" } → Issue
```

---

## Priority for tonight

1. `POST /api/calibration`, `GET /api/camera/stream`, `POST /api/baseline` (Setup → Workspace demo path)
2. Verdicts on `GET /api/observations` (review screen badges)
3. `GET/POST /api/projects` + `activate`
4. Stage/unstage + tested
5. Everything in 8–11
