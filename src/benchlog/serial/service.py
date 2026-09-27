"""One serial agent connection for the lifetime of the app.

Scans must keep working camera-only, so `snapshot()` never raises: an unplugged or silent agent
gives None. The background thread only ever PINGs (for the UI's status light); probing drives pins
through their pulls, so it happens only when a scan or the user asks for it.
"""

import threading
from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, Field

from benchlog.core.reconcile import SerialReadings
from benchlog.serial.agent_client import AgentClient, AgentDisconnected, AgentError, I2cResult, ProbeResult

PING_INTERVAL = 5.0


class PortInfo(BaseModel):
    device: str = Field(description="What to pass to connect, e.g. '/dev/cu.usbserial-0001'.")
    description: str


class SerialStatus(BaseModel):
    connected: bool
    port: str | None = None
    agent: str | None = Field(default=None, description="Firmware version reported by HELLO.")
    pins: list[int] = Field(default=[], description="GPIOs the agent may probe.")
    last_seen: str | None = Field(default=None, description="UTC time of the last reply from the agent.")
    last_error: str | None = None


class SerialSnapshot(BaseModel):
    """What the agent sensed at scan time."""

    port: str
    agent: str | None
    probe: ProbeResult
    i2c: I2cResult

    def readings(self) -> SerialReadings:
        """The raw readings, as the core saves them with a scan."""
        return SerialReadings(
            probed_at=self.probe.taken_at, port=self.port, agent=self.agent, pins=self.probe.pins, i2c=self.i2c.devices
        )


def available_ports() -> list[PortInfo]:
    try:
        from serial.tools.list_ports import comports
    except ImportError as e:
        raise AgentError("listing serial ports needs pyserial (`pip install pyserial`)") from e
    return [PortInfo(device=p.device, description=p.description or "") for p in comports()]


# USB-serial chips on ESP32 dev boards (CP210x, CH340, FTDI, native USB) and how they're named.
_ESP32_PORT_HINTS = ("usbserial", "slab_usbtouart", "wchusbserial", "usbmodem", "ttyusb", "ttyacm", "cp210", "ch340")


def find_esp32_port() -> str | None:
    """The first serial port that looks like an ESP32 dev board, or None."""
    try:
        ports = available_ports()
    except AgentError:
        return None
    for port in ports:
        name = f"{port.device} {port.description}".lower()
        # On macOS every device appears twice; /dev/cu.* is the one to open (tty.* waits for carrier).
        if any(h in name for h in _ESP32_PORT_HINTS) and not port.device.startswith("/dev/tty."):
            return port.device
    return None


class SerialService:
    def __init__(
        self,
        client_factory: Callable[[str], AgentClient] = AgentClient,
        ping_interval: float | None = PING_INTERVAL,
    ) -> None:
        self._client_factory = client_factory
        self._ping_interval = ping_interval
        self._client: AgentClient | None = None
        self._port: str | None = None
        self._last_seen: str | None = None
        self._last_error: str | None = None
        self._alive = False
        self._state_lock = threading.Lock()  # guards connect/close against each other
        self._stop = threading.Event()
        self._pinger: threading.Thread | None = None

    # ── Lifecycle ──

    def connect(self, port: str) -> SerialStatus:
        """Connect to the agent on `port`, replacing any current connection. Raises AgentError."""
        with self._state_lock:
            self._close_locked()
            self._port = port
            client = self._client_factory(port)
            try:
                client.connect()
            except AgentError as e:
                self._last_error = str(e)
                raise
            self._client = client
            self._mark_seen()
            self._start_pinger()
        return self.status()

    def close(self) -> None:
        with self._state_lock:
            self._close_locked()

    def _close_locked(self) -> None:
        self._stop.set()
        if self._pinger is not None and self._pinger is not threading.current_thread():
            self._pinger.join(timeout=5)
        self._pinger = None
        if self._client is not None:
            self._client.close()
        self._client = None
        self._alive = False

    def status(self) -> SerialStatus:
        client = self._client
        info = client.info if client else None
        return SerialStatus(
            connected=client is not None and client.connected and self._alive,
            port=self._port,
            agent=info.agent if info else None,
            pins=info.pins if info else [],
            last_seen=self._last_seen,
            last_error=self._last_error,
        )

    # ── Reads ──

    def snapshot(self) -> SerialSnapshot | None:
        """Probe every safe pin, then scan I2C. None if there is no agent to ask."""
        client = self._client
        if client is None or not client.connected:
            return None
        try:
            probe = client.probe()
            i2c = client.i2c()
        except AgentError as e:
            self._failed(client, e)
            return None
        self._mark_seen()
        return SerialSnapshot(port=client.port, agent=client.agent_version, probe=probe, i2c=i2c)

    def ping(self) -> bool:
        client = self._client
        if client is None or not client.connected:
            return False
        try:
            client.ping()
        except AgentError as e:
            self._failed(client, e)
            return False
        self._mark_seen()
        return True

    # ── Internals ──

    def _mark_seen(self) -> None:
        self._alive = True
        self._last_error = None
        self._last_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _failed(self, client: AgentClient, error: AgentError) -> None:
        self._alive = False
        self._last_error = str(error)
        if isinstance(error, AgentDisconnected):
            # The port is gone; the next connect() opens it again. No lock: connect() may be
            # holding it while it waits for this (pinger) thread to finish.
            if self._client is client:
                self._client = None

    def _start_pinger(self) -> None:
        if not self._ping_interval:
            return
        self._stop = threading.Event()
        self._pinger = threading.Thread(target=self._ping_loop, args=(self._stop,), name="serial-ping", daemon=True)
        self._pinger.start()

    def _ping_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self._ping_interval):
            if self._client is None:
                return
            self.ping()


def snapshot_once(port: str, client_factory: Callable[[str], AgentClient] | None = None) -> SerialSnapshot:
    """Connect, probe and scan I2C once, then disconnect (for the CLI). Raises AgentError.

    Opening the port reboots the ESP32, so this takes a few seconds.
    """
    service = SerialService(client_factory=client_factory or AgentClient, ping_interval=None)
    try:
        service.connect(port)
        snapshot = service.snapshot()
    finally:
        service.close()
    if snapshot is None:
        raise AgentError(service.status().last_error or "the serial agent didn't answer")
    return snapshot
