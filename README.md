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
