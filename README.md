# benchlog

Version control for breadboard prototypes. Scan the board, review what changed, commit it together with the firmware, and open a PR with automated checks.

## Layout

| Path | What | Owner |
|---|---|---|
| `src/benchlog/core/` | Circuit model, netlist, diff, git wrapper | Person 1 |
| `src/benchlog/core/checks/` | Circuit checks (local + CI) | Person 2 |
| `src/benchlog/cli/` | `benchlog` command (Typer) | Person 1 |
| `src/benchlog/server/` | Local FastAPI service for the UI | Person 1 / 4 |
| `src/benchlog/vision/` | Camera, calibration, scan diffing | Person 4 |
| `web/` | React + TypeScript UI (Vite) | Person 3 |
| `firmware/` | ESP32 demo sketch | Person 2 |
| `examples/` | Sample circuit JSON and board photos | Everyone |

## Setup

Python (3.11+):

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"      # or ".[dev]" if you don't need OpenCV/FastAPI
benchlog --help
pytest
```

Web:

```sh
cd web
npm install
npm run dev                  # proxies /api to http://127.0.0.1:8000
```

Run the API with `benchlog serve`.

## Baseline capture

Scans an empty BB830 ("mobile check deposit" style: line the board up in a guide outline,
hold steady, auto-capture) and saves it as the baseline future scans get diffed against.
Requires the `vision` extra (`pip install -e ".[vision]"` or `".[all]"`).

```sh
python -m benchlog.vision.capture                       # live camera, camera index 0
python -m benchlog.vision.capture --camera 1             # try 1 or 2 if 0 is the wrong device
python -m benchlog.vision.capture --image board.jpg      # a still photo instead of a camera
```

### iPhone camera on a Mac (Continuity Camera, no extra apps)

This is the setup for the live demo. With the iPhone and MacBook on the same Apple ID, the
iPhone shows up as a normal webcam that `--camera N` can open.

Requirements:
- macOS 13 Ventura or later; iOS 16 or later on an iPhone XR or newer
- Same Apple ID on both, with two-factor authentication on
- Wi-Fi and Bluetooth on for both, devices near each other
- On the iPhone: Settings → General → AirPlay & Continuity → **Continuity Camera** on

Steps:
1. Mount the iPhone pointing straight down at the board, **locked and not moving** (stand, clamp,
   or a stack of books). Continuity Camera only works while the phone is locked and still;
   hand-held it keeps dropping out. Mount it so the board's long edge runs along the phone's
   long edge (landscape) if your mount allows it -- that gives the board the most pixels. A
   portrait-oriented frame (e.g. the phone mounted the other way) is auto-rotated before
   detection, so it still works, just with less resolution across the board's long axis.
2. Set up the repo on the Mac:
   ```sh
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[all]"
   ```
3. Run `python -m benchlog.vision.capture --camera 1`. The first run triggers a macOS camera
   permission prompt for Terminal (or your IDE); allow it and run again. If you see the Mac's
   built-in camera instead of the iPhone, try `--camera 0` or `--camera 2`. The live preview
   window is capped to a laptop-friendly size and tolerates the occasional dropped frame
   (Continuity Camera hiccups); if it can't open a camera at all or frames stop coming in, it
   prints which `--camera` indices to try and where to check camera permissions.

On Windows there is no built-in equivalent: use a webcam app such as
[Camo](https://reincubate.com/camo/) or iVCam (installed on both phone and PC), or take the photo
on the phone and use `--image`.

Put the board on a dark, matte surface, light it evenly, and keep the phone parallel to the
board. The baseline (raw photo, warped scan, annotated hole map, and `baseline.json`) is
written to `.benchlog/baseline/` by default (`--out DIR` to change it); see
`src/benchlog/vision/capture.py`'s module docstring for details.

**macOS gotcha:** if `import benchlog` or the `benchlog` command suddenly fails with
`ModuleNotFoundError`, macOS (usually iCloud Desktop/Documents sync) has marked the editable-install
`.pth` file hidden and Python 3.12 skips hidden `.pth` files. Fix with
`chflags nohidden .venv/lib/python*/site-packages/*.pth`, or keep the repo outside iCloud-synced folders.
`pytest` works either way.
