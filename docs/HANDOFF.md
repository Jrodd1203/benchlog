# benchlog handoff — 2026-09-27 05:20 EDT (~6 h to submission)

Snapshot of `main` at `7132379`. Everything below was checked by running it, not read off the
code, unless marked *unverified*.

## TL;DR

- The demo story works end to end in the UI: demo scan → review → accept → commit → timeline,
  and the `move-sensor` PR fails its check with "GPIO12 held high at boot can stop the ESP32 from
  starting".
- CI is green on `main`, 322 tests. The frontend builds.
- Frontend and backend are both deployed on Railway and linked.
- Biggest risks: the real camera scan on stage (untested here), the deployed site shows no
  projects until someone creates one, and a handful of rough edges judges will see (listed below).
- **Grade as it stands: B+.** An A- is within reach in the next 6 h through demo-readiness fixes
  and rehearsal. It doesn't need new features.

## Status against the plan (docs/notion-plan.md)

| # | Feature | Status |
|---|---|---|
| 1 | Projects: list, create, open | Works (UI + API). Dates show as raw ISO strings. |
| 2 | Setup wizard | Present. Shows dev text ("Saving the choice needs PUT /api/circuit"). |
| 3 | Workspace: virtual board, Scan, ESP32 light | Works. Board takes ~5 s to appear. |
| 4 | Proposal review | Works: accept / reject / fix end / view photo, verdict chips. |
| 5 | Serial (ESP32) | Screen wired to the API. *Unverified with real hardware.* |
| 6 | Changes and commits | Works. "Stage firmware" and "Mark as tested" still say "(soon)". |
| 7 | Timeline | Works on real git history, plus a Build guide tab. ~3 s to load. |
| 8 | Diff | Wired to the API. |
| 9 | Checks | Works: power short, unconfirmed wires, serial conflicts, I2C, ESP32 pins; pass / fail / n/a. `benchlog check` exits 1 on failure, so it can gate CI. |
| 10 | Branches and PRs | Works locally: branch, switch, open PR, before/after board, checks, merge. PRs live in `.benchlog/prs/`, **not GitHub**. |
| 11 | Issues | Stub screen only. |
| — | Camera scan (vision) | Code present with tests. *Unverified on the demo Mac + camera.* |
| — | Push → GitHub PR → Action posts results (plan step 5) | **Not built, by agreement** ("Local first, GitHub later" in `docs/api-endpoints-for-ui.md`). `check.yml` runs this repo's pytest, not project checks. |

## Demo script (the path that works today)

Seed the demo projects first: `benchlog demo seed`, then `benchlog serve`.

1. Projects → **Pot + LED** → Workspace. (It opens on Setup: click Workspace.)
2. **Scan.** No camera, or it misfires: the error box offers **Run a demo scan** (moves `w5`
   A4 → A12). That button only shows *after* a failed scan.
3. **Review 1 change** → Accept → "GPIO12 connected to pot1.wiper…" → **Commit these changes**
   → type a message → **Commit circuit** → *See it on the timeline*.
4. **Branches & PRs** → PR #1 "Move sensor to GPIO12" → before/after board, "What changes",
   check **fail** with the strapping-pin explanation.
5. Contrast: **LED bar** project → PR "Add a third LED" → check **pass** → merge.
6. **Timeline** → scrub the history, Build guide tab.

For the real story on stage, do step 3 on a branch (Branches & PRs → Create branch → Switch)
so the PR in step 4 is one you made live.

## Deployment (Railway)

| Service | Root dir | Builder | Variables |
|---|---|---|---|
| Frontend | `/web` | Railpack (Vite static + Caddy) | `VITE_API_URL=https://<backend domain>` (baked in at build: redeploy after changing) |
| Backend | `/` | `Dockerfile` (python 3.13-slim + git) | `BENCHLOG_CORS_ORIGINS=https://<frontend domain>`, `PORT=8000` |

- Cloud backend can't scan or talk to an ESP32 (no hardware). Commits show "ESP32 check: skipped".
- Projects live in the container: **wiped on every backend redeploy** unless a volume is mounted
  at `/root/benchlog-projects`.
- The deployed API has **no auth**. Anyone with the URL can create projects. Fine for judging.
- Several duplicate Railway services exist from earlier attempts (`loyal-vitality`,
  `positive-curiosity`, `optimistic-truth`, …). Keep one backend, delete the rest.
- Local dev is unchanged: the Vite proxy still serves `/api`, and CORS stays off unless the
  variable is set.

## Known issues (ordered by what a judge would notice)

1. Deployed site starts with **zero projects** (no seed data in the container).
2. Opening a project lands on **Setup** instead of Workspace, even for projects with history.
3. Demo-scan fallback is hidden until a real scan fails.
4. Visible placeholders: "(soon)" buttons, dev text in Setup, Issues stub screen.
5. Workspace/Timeline take 3–5 s to load. Early `/api/history` calls fire before a project is
   selected (400s in the console). The "community circuits" feature 404s on sample GitHub repos
   that don't exist.
6. Review legend text runs together ("addedremovedcamera unsure"). Review heading has no left
   padding. Project dates are raw ISO strings.
7. Windows only: run with `PYTHONUTF8=1`. Without it, `benchlog demo seed` and 10 tests fail
   with encoding errors. With it, only `test_demo.py::test_seed_command` (`--reset`) still fails.
   CI (Linux) is green, and Mac is unaffected.

## Other notes in `docs/`

- `notion-plan.md`: the plan and requirements, imported from Notion on 2026-09-26.
- `api-endpoints-for-ui.md`: the UI↔API contract as planned on 2026-09-26. Partly superseded:
  projects are picked with `?project=<id>` (no `/activate`), and `/api/push`, `/api/stage` and
  `/api/issues` were never built.

## Housekeeping

- PR #15 `felix/wire-scan-flow` is 58 commits behind `main` with conflicts, and its flow is
  already on `main`. Close it, or cherry-pick only the commit-message prefill.
- Merged `ethan/*` deploy branches can be deleted.

## Grade as it stands: B+

Generic hackathon rubric. The event's own criteria weren't in the repo.

| Criterion | Grade | Why |
|---|---|---|
| Technical difficulty | A | CV pipeline + ESP32 cross-check + git-backed circuit model + netlist diffs + rule engine + FastAPI + React + 17-command CLI, 322 tests. |
| Innovation | A- | "What changed since this last worked?" for physical breadboards is a clear, novel angle vs Fritzing/Wokwi/AllSpice. |
| Functionality | B+ | Core loop and PR/check story work in the UI. Real camera path unverified. PRs are local-only (agreed scope), Issues is a stub. |
| Engineering quality | A- | Clean layering (light `core`, optional extras), typed models, CI green, seeded demo data. |
| Design / UX | B | Distinctive look and good board rendering. Placeholders and dev text leak through, and loads are slow. |
| Demo readiness | B- | Works, but you have to know the path (Setup detour, hidden fallback). The deployed site is empty. |

## Next 6 hours (freeze features by T-2 h)

1. **Rehearse on the demo Mac with the real board and camera (45 min).** Decide: live camera, or
   demo scan as plan B. Tape the stand. This is the biggest risk.
2. **Seed the deployed backend (10 min):** add `RUN benchlog demo seed` to the `Dockerfile`
   after the `pip install`. Judges who open the URL then see 3 projects with history and PRs.
3. **Demo-readiness polish (60–90 min, `web/`):** open projects on Workspace when they have
   history; always show "Run a demo scan"; hide the "(soon)" buttons and the dev text in Setup;
   format dates; fix the legend spacing.
4. **Pitch (60 min):** a 3-minute script following the demo above. Lead with the GPIO12 failure.
   Answer "does it use GitHub?" with: every project is a real git repo, and `benchlog check`
   exits non-zero on failure, so the same checks drop into any CI. (Optional 20 min: add a
   sample workflow for project repos to prove it.)
5. **Record a backup video** of the full demo path.
6. Close PR #15, delete stale branches, delete duplicate Railway services.
