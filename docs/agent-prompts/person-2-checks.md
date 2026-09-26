You are working as Person 2 (Checks + GitHub) on benchlog. Read CLAUDE.md, `src/benchlog/core/`,
`examples/`, and `examples/circuits/README.md` first. You own `src/benchlog/core/checks/`,
`.github/workflows/`, and `firmware/`.

Tasks, in order. Propose a short plan first, then do one task at a time and stop after each so I can review:

1. **Result model**: a `CheckResult` (rule id, status `pass | fail | needs_confirmation | unsupported`,
   message, involved components/holes). Draft it inside `core/checks/`; I'll get team sign-off before it
   moves into `models.py`.
2. **Connectivity**: rules need to know which ESP32 pin each component pin is connected to. Person 1 is
   building `core/netlist.py`. If it isn't there yet, write a minimal private helper and swap it out later.
3. **ESP32 rules** (`core/checks/esp32.py`), each with a clear message explaining the physical consequence:
   - strapping pins (0, 2, 12, 15) driven by something that could hold them at the wrong level at boot.
     Must **fail on `moved-wire.json` and pass on `working.json`**. This is the demo.
   - input-only pins (34–39) wired to something that needs an output (e.g. an LED)
   - flash pins (6–11) used at all
   - a wire directly connecting the + and - rails / 3V3 and GND
   - unresolved (pending) observations → `needs_confirmation`
4. **`pins.h` generation**: from the circuit, emit e.g. `#define POT1_WIPER_PIN 34`, `#define LED1_ANODE_PIN 13`.
   Write the demo sketch in `firmware/benchlog_demo/` against it (read the pot, set LED brightness).
   Add a rule that fails if the sketch doesn't use the generated header.
5. **CLI + CI**: implement `benchlog check` (Rich table output, non-zero exit on failure) and add it to
   `.github/workflows/check.yml` so it runs on the example/project circuit in a PR.
6. **PR comment** (stretch): the workflow posts check results as a PR comment.

Done when a branch containing the moved-wire circuit turns CI red with a message a judge can understand.
Add tests for every rule and keep `pytest` green.
