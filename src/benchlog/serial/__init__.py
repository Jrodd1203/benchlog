"""Talk to the benchlog ESP32 serial agent (firmware in `agent/`).

The agent reports what each safe GPIO electrically senses (floating, pulled low/high) and which I2C
devices answer. pyserial is imported lazily, so the rest of benchlog works without it.
"""
