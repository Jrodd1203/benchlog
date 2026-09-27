<div align="center">
  <img src="docs/logo.png" alt="benchlog" width="280" />
</div>

---

Version control for breadboard prototypes. Scan the board, review what changed, commit it together with the firmware, and open a PR with automated checks — the same workflow as software, applied to hardware.

## Try it in two commands

```sh
benchlog demo seed   # creates ~/benchlog-projects/ with three real git repos
benchlog serve       # opens http://localhost:8000 · UI at http://localhost:5173
```

The projects screen lists the demos. Every screen works on them — scan, review, diff, timeline, PRs.

- **pot-led** — branch `move-sensor` has an open PR that **fails checks** (the pot moved onto GPIO12, a strapping pin that can stop the ESP32 from booting). Can't be merged until the wire is fixed.
- **weather-station** — ESP32 + BME280 over I²C. The wiring came in through a PR, tested and merged.
- **led-bar** — branch `third-led` has an open PR that passes all checks and is ready to merge.

## The workflow

```sh
benchlog scan                             # camera reads the board, proposes changes
benchlog review                           # accept, reject, or correct each one
benchlog commit -m "Move pot to GPIO12"   # saves circuit + firmware as one commit
benchlog check                            # electrical checks run locally and in CI
```

## Setup

**Python (3.11+)**

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"      # or "[dev]" without OpenCV/FastAPI
benchlog --help
pytest
```

**Web UI**

```sh
cd web && npm install
npm run dev         # proxies /api → http://127.0.0.1:8000
```

**API server**

```sh
benchlog serve                          # project containing this folder, port 8000
benchlog serve --project ~/my-board    # a project elsewhere
benchlog serve --host 0.0.0.0          # reachable on the local network
benchlog serve --reload                # restart on code changes (drops ESP32 link)
```

API docs: http://localhost:8000/docs

> **macOS gotcha:** if `import benchlog` or `benchlog` suddenly fails with `ModuleNotFoundError`, iCloud sync has marked the editable-install `.pth` file hidden and Python 3.12 skips it. Fix: `chflags nohidden .venv/lib/python*/site-packages/*.pth`, or keep the repo outside iCloud-synced folders.

## Camera setup

Mount a webcam or iPhone (Continuity Camera) pointing straight down at the board. Calibrate once — scans then lock on even if the board gets nudged.

```sh
benchlog camera list          # which indices work, at what resolution
benchlog camera preview 1     # live: green outline = board steady, ready to scan
benchlog camera use 1         # remember this camera for this workstation
benchlog camera calibrate     # map every hole (board empty), Enter to save
benchlog camera preview       # verify: hole dots should follow the board if nudged
benchlog scan
```

`camera calibrate` draws every hole on the live image. Dots snap automatically on an opaque board; on translucent boards drag the corner handles (or nudge with arrow keys / `ijkl`) until every dot sits in its hole. Use `--board-matches-circuit` if the board was already built. Recalibrate if the camera moves or its resolution changes.

### iPhone on Mac (Continuity Camera)

Requirements: macOS 13+, iOS 16+ (iPhone XR or newer), same Apple ID, Wi-Fi and Bluetooth on, Settings → General → AirPlay & Continuity → Continuity Camera on.

1. Mount the iPhone pointing straight down, **locked and still**. Continuity Camera only works while locked; hand-held it keeps dropping out. Landscape gives the board more pixels.
2. Run `benchlog camera list` and try indices 0–2. The first run triggers a macOS camera permission prompt — allow it and run again.

On Windows: use [Camo](https://reincubate.com/camo/) or iVCam, or shoot a photo and use `--image board.jpg`.

## ESP32 serial agent

With the agent flashed on the ESP32 (`agent/`) and plugged in, benchlog cross-checks the camera's work against what the pins actually read.

```sh
benchlog serial list          # find available ports
benchlog serial use /dev/cu.… # remember this port
benchlog serial probe         # verify the agent is responding
```

- **Scan:** each change shows "ESP32 confirms", "ESP32 disagrees" (with what to fix), or "can't tell".
- **Commit:** the circuit is checked against the board. Failure blocks the commit (`--force` overrides). Result is recorded as `ESP32-Check: passed`, `failed (forced)`, or `skipped`.

> The port can only be open in one program. While `benchlog serve` holds the ESP32 connection, CLI scans find it busy — scan from the web UI instead, or use `benchlog scan --no-serial`.

## Repo layout

| Path | What |
|---|---|
| `src/benchlog/core/` | Circuit model, netlist, diff, git wrapper |
| `src/benchlog/core/checks/` | Circuit checks (local + CI) |
| `src/benchlog/cli/` | `benchlog` command (Typer) |
| `src/benchlog/server/` | Local FastAPI service for the web UI |
| `src/benchlog/vision/` | Camera, calibration, scan diffing |
| `web/` | React + TypeScript UI (Vite) |
| `firmware/` | ESP32 demo sketch |
| `examples/` | Sample circuit JSON and board photos |
