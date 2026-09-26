You are working as Person 3 (Frontend) on benchlog. Read CLAUDE.md, `src/benchlog/core/models.py`,
`src/benchlog/core/board.py`, and `examples/` first. You own `web/` (Vite + React + TypeScript). The dev
server proxies `/api` to the FastAPI service on port 8000, but **don't depend on the API yet**: import the
example JSON files directly and put all data access behind a small `api.ts` module so it can be swapped
to real `fetch` calls later.

Tasks, in order. Propose a short plan first, then do one task at a time and stop after each so I can review:

1. **Types**: TypeScript types matching `Circuit`, `Component`, `Wire`, `Observation`. Person 1 is adding a
   `benchlog schema` command; generate from it when it exists, hand-write a matching version until then.
2. **Breadboard SVG**: a component that draws a bb830 board from its geometry (63 rows, columns A–J with
   the center gap, 4 rails of 50 holes), with hole names on hover. Keep geometry (hole name → x/y) in one
   module; the diff view, click-to-wire, and photo overlays all reuse it.
3. **Render a circuit**: draw `examples/circuits/working.json`: ESP32 as a rectangle over rows 1–15,
   other components as simple labeled shapes across their pins, wires as colored lines/curves between holes.
4. **Diff view**: compare `working.json` and `moved-wire.json`. Highlight w5's old end in red and new end
   in green, dim unchanged items, and list the change in a side panel.
5. **Click-to-wire**: click two holes to create a wire; click a wire to remove it.
6. **Review screen**: show `examples/observations/moved-wire.json` with accept/reject buttons and
   highlight any `uncertain_holes`.
7. **Commit dialog + timeline slider** with fake history, then a **checks panel** with pass/fail badges.

Keep it clean and simple: one clear layout, board in the middle, panels on the side. Done when I can click
through working → moved wire → review → commit visually. Run `npm run build` before finishing each task.
