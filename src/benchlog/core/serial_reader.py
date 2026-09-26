"""Continuous ESP32 serial reader. Parses JSON pin-state lines into PinSnapshot objects."""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator

import serial  # pyserial


@dataclass
class PinSnapshot:
    t: int                          # ESP32 millis()
    digital: dict[int, int]         # gpio_num -> 0|1
    analog: dict[int, int]          # gpio_num -> 0-4095

    @staticmethod
    def from_line(line: str) -> "PinSnapshot | None":
        try:
            d = json.loads(line.strip())
            return PinSnapshot(
                t=int(d["t"]),
                digital={int(k): int(v) for k, v in d.get("d", {}).items()},
                analog={int(k): int(v) for k, v in d.get("a", {}).items()},
            )
        except Exception:
            return None

    def is_stable(self, gpio: int, window: "list[PinSnapshot]", tolerance: int = 2) -> bool:
        """Return True if this GPIO reads the same value across the window (not floating)."""
        vals = [s.digital.get(gpio) for s in window if gpio in s.digital]
        if len(vals) < 3:
            return False
        return max(vals) - min(vals) <= tolerance  # type: ignore[operator]


class SerialReader:
    """Background thread that continuously reads pin snapshots from the ESP32.

    Usage:
        reader = SerialReader("/dev/ttyUSB0")
        reader.start()
        snap = reader.latest()   # most recent snapshot
        window = reader.window() # last N snapshots for stability checks
        reader.stop()
    """

    def __init__(self, port: str, baud: int = 115200, window_size: int = 10) -> None:
        self._port = port
        self._baud = baud
        self._window: deque[PinSnapshot] = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def latest(self) -> PinSnapshot | None:
        with self._lock:
            return self._window[-1] if self._window else None

    def window(self) -> list[PinSnapshot]:
        with self._lock:
            return list(self._window)

    def gpio_stable_high(self, gpio: int) -> bool:
        """True if the given GPIO has been consistently HIGH over the last window."""
        w = self.window()
        if len(w) < 3:
            return False
        vals = [s.digital.get(gpio, 0) for s in w]
        return all(v == 1 for v in vals)

    def gpio_stable_low(self, gpio: int) -> bool:
        w = self.window()
        if len(w) < 3:
            return False
        vals = [s.digital.get(gpio, 0) for s in w]
        return all(v == 0 for v in vals)

    def gpio_floating(self, gpio: int) -> bool:
        """True if GPIO is reading inconsistently — likely nothing connected."""
        w = self.window()
        if len(w) < 3:
            return True
        vals = [s.digital.get(gpio, 0) for s in w]
        return len(set(vals)) > 1

    def _run(self) -> None:
        try:
            with serial.Serial(self._port, self._baud, timeout=1) as ser:
                while self._running:
                    raw = ser.readline()
                    if not raw:
                        continue
                    snap = PinSnapshot.from_line(raw.decode("utf-8", errors="replace"))
                    if snap is not None:
                        with self._lock:
                            self._window.append(snap)
        except serial.SerialException as e:
            print(f"[serial] {e}")


def find_esp32_port() -> str | None:
    """Best-effort guess at the ESP32's serial port."""
    import glob
    import sys

    if sys.platform == "darwin":
        candidates = glob.glob("/dev/cu.usbserial-*") + glob.glob("/dev/cu.SLAB_USBtoUART*")
    else:
        candidates = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    return candidates[0] if candidates else None
