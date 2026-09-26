Hand-written example circuits for the demo (DRAFT, edit at kickoff).

Demo circuit on a full-size 830-point board:

- ESP32 DevKit V1 (30-pin) in rows 1-15, pins in columns B and I (only A and J are free beside it)
- 3V3 -> R+ rail (w1), GND -> R- rail (w2)
- 10kΩ pot in H20-H22: ends to the rails (w3, w4), wiper -> GPIO34 via A4 (w5, yellow)
- GPIO13 -> 220Ω resistor (A14-A18) -> red LED (E18/F18) -> R- rail (w6)

Files:

- `empty.json`: empty board
- `working.json`: the working demo circuit
- `moved-wire.json`: w5 moved from A4 (GPIO34) to A12 (GPIO12, a strapping pin: the pot can hold it
  high at boot, which selects 1.8 V flash and stops the ESP32 from booting)

`../observations/moved-wire.json` is what a scan should propose for that change.
