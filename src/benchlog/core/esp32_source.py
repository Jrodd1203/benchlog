"""Protocol that any ESP32 data source must satisfy.

The reconciler depends only on this interface — not on SerialReader directly.
Swap in the real SerialReader when the ESP32 is connected, or a stub/mock for
vision-only mode and testing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Esp32Source(Protocol):
    """Minimal interface the reconciler needs from an ESP32 data source."""

    def gpio_stable_high(self, gpio: int) -> bool:
        """True if the GPIO has been consistently HIGH over recent samples."""
        ...

    def gpio_stable_low(self, gpio: int) -> bool:
        """True if the GPIO has been consistently LOW over recent samples."""
        ...

    def gpio_floating(self, gpio: int) -> bool:
        """True if the GPIO is reading inconsistently (nothing connected)."""
        ...

    def window(self) -> list:
        """Return the last N pin snapshots (newest last)."""
        ...

    def is_alive(self) -> bool:
        """True if the source is still receiving data (not disconnected)."""
        ...


class NullEsp32Source:
    """Stand-in when the ESP32 is not connected.

    All observations pass through as vision-only with unmodified confidence.
    Drop-in replacement: pass this instead of None so call sites stay uniform.
    """

    def gpio_stable_high(self, gpio: int) -> bool:
        return False

    def gpio_stable_low(self, gpio: int) -> bool:
        return False

    def gpio_floating(self, gpio: int) -> bool:
        return False

    def window(self) -> list:
        return []

    def is_alive(self) -> bool:
        return False
