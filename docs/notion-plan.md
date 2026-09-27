# benchlog — Notion plan (imported notes)

Source: [Bench Log Notion page](https://app.notion.com/p/Bench-Log-3e7395097f1c803bbec2f58de8eb5e6a?pvs=11).
Copied here on 2026-09-26 so the plan is readable without Notion access. If the Notion page
changes, this file will drift — treat it as a snapshot, not the live source of truth.

## Architecture

```
Camera (USB webcam)              ESP32 agent (over USB serial)
        |                                 |
        v                                 v
   Vision (OpenCV diff)             Serial (pin probes, I2C)
        |                                 |
        +---------------> Reconciler <----+  <--->  Web app (React, the product)
                       (proposals you           <--->  CLI (same actions)
                        accept or fix)
                              |
                              v
                        Circuit model
                     (reads/writes circuit.json)
                          /         \
                         v           v
                     Checker       Git layer
                   (rule checks)  (commits, merges)
                          \         /
                           v       v
                        Project folder
                   (circuit.json, PRs, issues, .git)
```

All of this runs behind a FastAPI backend. Camera → Vision and ESP32 agent → Serial feed the
Reconciler, which produces proposals the user accepts or corrects. The web app and CLI both talk
to the same circuit model, checker, and git layer underneath.

## Core Features

- [ ] **Board calibration.** Map the camera image to named breadboard holes and know which holes
      are internally connected. Without this, detection has nothing to anchor to.
- [ ] **Scan and change detection.** Compare a new capture to the last accepted state and propose
      what changed: a wire added, removed, or moved, with its exact endpoints. This is the heart
      of the project and the riskiest part.
- [ ] **Confirmation of uncertain detections.** When the camera isn't sure, the user picks the
      right hole instead of the system guessing. This keeps your data trustworthy.
- [ ] **Circuit data model.** One `circuit.json` with stable component IDs, placements, and wires,
      from which you derive the electrical connections. Every other feature reads from it.
- [ ] **Visual diff.** An SVG breadboard diagram that highlights what changed between two states.
      This is what makes the scan result understandable to a judge in seconds.
- [ ] **Status, stage, and commit (Git-backed).** Show pending changes, stage the circuit and
      firmware, and commit them together, through both the CLI and the interface.
- [ ] **Commit timeline.** A slider that loads and renders the circuit at each commit. This proves
      the history is real and shows reproducibility.
- [ ] **Circuit checks.** Two deterministic rules: power-to-ground short and firmware pin
      mismatch. Results are Pass, Fail, or Needs confirmation.
- [ ] **PR with checks.** Push a branch, create a real PR from your app, run the checker in
      GitHub Actions, and show results in your interface.

[PR plan](https://app.notion.com/p/PR-plan-3e7395097f1c80518d83e6ef27079c16?pvs=21)

### Valuable after core is working

- Marking a commit as physically tested, then comparing the board against it
- Component-linked issues
- A curated build guide for rebuilding or forking
- Support for resistors and LEDs beyond wires

### Connection of serial and camera

Camera basically maps out the connection of the pins while the serial works as a check to see if
the ESP32 (or whichever controller) "feels" what the camera is seeing. If both the camera and
serial agree then the change is approved and it is tracked. If they are different then the user
needs to come in and decide the correct version.

---

## Team plan

**One-liner:** Version control for breadboard prototypes. Scan the board, review what changed,
commit it together with the firmware, and open a PR with automated checks.

**Hook:** "What changed since this last worked?"

### Demo path (the only flow we build for)

Physical change → scan → review → visual diff → commit → timeline → push → PR → check result

Demo story: a working ESP32 circuit is committed. A teammate moves the sensor wire onto **GPIO
12** (a strapping pin). Scan detects the moved wire → commit → PR → the check fails and explains
that this pin can break booting. The teammate moves the wire to a safe pin, rescans, pushes, the
check passes, and the PR gets approved. The timeline shows the full history.

### Key decisions

- **Diff based on scans.** Compare each scan to the last accepted state, instead of recognizing
  the whole circuit from scratch.
- **Manual wiring is a first-class feature.** "Click two holes to add a wire" must work well on
  its own. The camera speeds this up; it isn't required.
- **Netlist first.** Diffs show electrical changes first and placement changes second. A wire
  moved within the same connected strip = placement change only.
- **Simple user flow:** scan → review → commit. Staging is implicit for the circuit; firmware can
  be staged separately.
- **Physical HEAD:** track which commit the real board currently matches. Show "board differs
  from HEAD" and a rebuild diff after a checkout.
- **No circuit merges yet:** fast-forward / "adopt" only. A conflict means a rebuild is required.
- **Generate `pins.h` from the circuit** instead of parsing arbitrary C++. The check confirms the
  firmware uses the generated header.
- **ArUco markers** on the board corners for automatic calibration.
- **Canonical JSON:** sorted keys, stable ordering, stable component and wire IDs, so raw
  `git diff` stays readable.
- **Images:** Git LFS, or commit only compressed crops and diff overlays.

### Tech stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Frontend | React + TypeScript | TS types generated from the Pydantic JSON Schema |
| Diagrams | SVG | |
| CV | Python + OpenCV + NumPy | `cv2.aruco` for calibration |
| Data model | Pydantic + JSON | Single source of truth for the schema |
| Local service | FastAPI | |
| CLI | Typer + Rich | Thin wrapper over the core package |
| Version control | `git` via subprocess | Simpler than GitPython |
| Remote | GitHub + Actions | `gh pr create` for PRs, no OAuth flow |

**Package layout:** a lightweight `core` package (model, netlist, diff, checks) with the camera
code and the server as optional extras, so CI installs quickly.

### Checks (first version)

- [ ] Direct wire between supply and ground
- [ ] ESP32 input-only pins (GPIO 34–39) used as outputs
- [ ] ESP32 strapping pins (GPIO 0, 2, 12, 15)
- [ ] ESP32 flash pins (GPIO 6–11) used
- [ ] Firmware pins don't match the generated `pins.h`
- [ ] Unresolved observations block a reliable check

Results are pass / fail / needs confirmation / unsupported. Each result belongs to one specific
commit.

### Roles

| Person | Owns | Done when |
| --- | --- | --- |
| **1. Core ("the spine")** | Pydantic models, breadboard template (which holes are connected), netlist, circuit diff, Git wrapper, commit/history, Typer CLI | `init / status / diff / commit / log` work on hand-written JSON |
| **2. Checks + GitHub** | Rule engine, ESP32 rules, `pins.h` generation, GitHub Action, PR creation, PR comment, demo firmware | `benchlog check` works locally, and the same check runs in CI on a pushed branch |
| **3. Frontend** | SVG breadboard view, click-to-wire, diff highlights, observation review, commit dialog, timeline slider, checks panel | Full UI flow works against fake data, then against the real API |
| **4. Camera / CV** | Webcam capture, ArUco calibration, hole grid overlay, before/after hole occupancy, pairing endpoints, `Observation` output, `/scan` endpoint | Given two photos, returns "wire added A12→F12" with a confidence score |

Names: 1 = ___ · 2 = ___ · 3 = ___ · 4 = ___

### First 1–2 hours (everyone together)

- [ ] Agree on the circuit schema: Breadboard template, Component, Wire, Hole, Net, Observation
- [ ] Commit the models and 3 example JSON files: empty board, working circuit, moved wire
- [ ] Agree on the API: `POST /scan`, `GET /status`, `GET /diff`, `POST /observations/{id}/accept`, `POST /commit`, `GET /history`, `POST /push`, `POST /pr`
- [ ] Write the exact demo script
- [ ] Lock in the parts: breadboard, ESP32, components

### Priorities (each step is a demo we could present if time ran out)

- [ ] 1. Schema, example files and demo script (everyone)
- [ ] 2. Manual wiring → diff → commit → timeline (Persons 1 + 3)
- [ ] 3. A check fails on the demo circuit, locally (Person 2)
- [ ] 4. **Integration checkpoint** at ~⅓ time: UI + service + Git + checks on manual input
- [ ] 5. Push → PR → Action posts check results (Persons 2 + 1)
- [ ] 6. Camera scan feeds the review screen (Person 4)
- [ ] 7. Polish: before/after images in the PR comment, evidence photos, "mark as tested"
- [ ] 8. Stretch: issues, build guide, Wokwi export, serial log as evidence, guided bisect

### Rules to protect the demo

- **Feature freeze** at ~hour 28–30 (for a 36h event). After that, only bug fixes and rehearsal.
- **Fallback:** a "load sample scan" button and a pre-recorded scan in case lighting fails.
- **Mark the camera stand** position with tape.
- **Merge to `main` every few hours.** No big integration at the end.
- **Pitch owner:** ___ (whoever has the most slack late: usually Person 3 or 4). Makes the slides
  and rehearses the 3-minute run-through.

### Prior art (how we're different)

- **Fritzing / Tinkercad:** you draw the circuit by hand; no version control.
- **Wokwi:** simulation only; no physical board.
- **AllSpice / CADLab:** Git-style review for PCB and schematic files, not breadboards.
- **Toastboard / CircuitSense:** research prototypes that instrument or sense the breadboard.
- **benchlog:** connects the physical workbench to Git history, reviews and reproducible builds.

### Open questions

- [x] Final name (benchlog?)
- [ ] Which sensor and which demo circuit?
- [ ] Webcam or a phone used as a webcam?
- [ ] Git LFS or compressed crops only?

---

## Benchlog Web App — Requirements

### 1. Projects

- List of your hardware projects (saved locally)
- Create a new project or open an existing one
- Everything reloads from files when the app restarts

### 2. Setup (one time per project)

- Pick the breadboard type
- Place the ESP32 and record where its pins sit
- Calibrate the camera (mark corners, check the hole overlay lines up)
- Live camera feed shown only during calibration
- Declare your parts (resistors, LEDs, sensors, values)
- Take the baseline photo

### 3. Workspace (main screen)

- Virtual breadboard showing every part and wire in its real holes
- Scan button: takes a photo + serial probe behind the scenes
- No live camera feed; the virtual board is the main view
- ESP32 status light

### 4. Proposal review

- Detected changes highlighted on the virtual breadboard (added = green, removed = red, moved =
  yellow)
- Verdict badge on each change: confirmed, conflict, camera only
- Accept or reject each change
- If the camera isn't sure, pick the right hole on the virtual board
- Optional "view photo" to see the camera crop for that change

### 5. Serial (ESP32)

- Port picker and Connect button
- Status light: ESP32 connected / not connected
- Warnings like "wire may not be seated" or "sensor not responding"

### 6. Changes and commits

- See unstaged and staged changes
- Stage the circuit, write a message, commit
- Optional "mark as tested" on a commit

### 7. Timeline

- Slider through every commit
- Virtual breadboard redraws the circuit at each version
- Highlights what changed from the previous commit

### 8. Diff

- Compare any two versions: parts and wires added, removed, or moved
- Shows whether a change is electrical or just a different hole

### 9. Checks

- Power-to-ground short
- Unconfirmed wires
- Camera vs. serial conflicts
- I2C sensor not responding
- Results: pass / fail / needs confirmation

### 10. Branches and PRs

- Create a branch to try a change without breaking main
- Open a PR: before/after virtual breadboard for the two branches
- Checks run automatically on the PR
- Merge button (only when checks pass)

### 11. Issues

- Report a problem tied to a specific part and commit
- Expected vs. observed behavior

### Priority

- **Core (demo needs these):** 1–7
- **Next:** 8–11
- **Later / stretch:** guided rebuild, 3D view, VS Code extension, online sharing between
  computers
