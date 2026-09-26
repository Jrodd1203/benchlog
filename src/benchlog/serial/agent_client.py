"""Client for the ESP32 serial agent.

Protocol: send one line "<id> <COMMAND> [args]", get back one line of compact JSON that always has
"id" and "ok". The agent prints nothing else except {"ready":true,"agent":"..."} at boot, but noise
on the line (a reset, a loose cable) can still show up, so anything that isn't a reply to the
request in flight is skipped.
"""

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

BAUD = 115200
READY_TIMEOUT = 5.0  # the ESP32 resets when the port opens and boots in about a second
I2C_TIMEOUT = 6.0  # a scan is fast, but a shorted SDA/SCL makes every address wait for a timeout

PinState = Literal["floating", "pulled_low", "pulled_high", "unstable", "unsafe"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── Results ───────────────────────────────────────────────────────────────────


class _Result(BaseModel):
    taken_at: str = Field(default_factory=_now, description="UTC time the reply arrived.")


class PingResult(_Result):
    uptime_ms: int


class HelloResult(_Result):
    board: str
    agent: str = Field(description="Firmware version, e.g. '0.1.0'.")
    pins: list[int] = Field(description="GPIOs the agent may probe.")


class ProbeResult(_Result):
    pins: dict[int, PinState] = Field(description="GPIO -> what it senses; 'unsafe' pins were not touched.")


class I2cResult(_Result):
    sda: int
    scl: int
    devices: list[str] = Field(description="Addresses that answered, e.g. ['0x76'].")


class ReadResult(_Result):
    pin: int
    digital: int
    analog_mv: int | None = Field(description="Only on ADC1 pins (GPIO 32-39).")


# ── Errors ────────────────────────────────────────────────────────────────────


class AgentError(Exception):
    """Anything that stops a request from getting an answer."""


class PortBusy(AgentError):
    pass


class AgentTimeout(AgentError):
    pass


class AgentDisconnected(AgentError):
    """The port went away mid-session (usually the USB cable was unplugged)."""


class AgentCommandError(AgentError):
    """The agent answered with ok:false."""

    def __init__(self, command: str, code: str) -> None:
        super().__init__(f"agent rejected {command}: {code}")
        self.command = command
        self.code = code


# ── Port ──────────────────────────────────────────────────────────────────────


class Port(Protocol):
    """The part of `serial.Serial` the client uses; tests pass a fake."""

    def readline(self) -> bytes: ...
    def write(self, data: bytes) -> int | None: ...
    def close(self) -> None: ...


def open_serial(port: str, baud: int) -> Port:
    try:
        import serial
    except ImportError as e:
        raise AgentError("the serial agent needs pyserial (`pip install pyserial`)") from e
    try:
        # A short read timeout lets the client keep its own deadline across partial lines.
        return serial.Serial(port, baud, timeout=0.05, exclusive=True)
    except serial.SerialException as e:
        text = str(e).lower()
        if "busy" in text or "lock" in text or "errno 16" in text or "access is denied" in text:
            raise PortBusy(
                f"another process is using the port {port} (close `pio device monitor` or the Arduino "
                "serial monitor and try again)"
            ) from e
        raise AgentError(f"can't open {port}: {e}") from e


class AgentClient:
    def __init__(self, port: str, baud: int = BAUD, timeout: float = 2.0, opener=open_serial) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.agent_version: str | None = None
        self.info: HelloResult | None = None
        self._opener = opener
        self._serial: Port | None = None
        self._buffer = b""
        self._next_id = 1
        self._lock = threading.Lock()

    # ── Lifecycle ──

    def connect(self, ready_timeout: float = READY_TIMEOUT) -> HelloResult:
        """Open the port, wait for the boot line, then say HELLO.

        Some USB adapters don't reset the board on open, so a missing boot line is not fatal:
        HELLO is what proves an agent is there.
        """
        if self._serial is not None:
            raise AgentError(f"already connected to {self.port}")
        self._serial = self._opener(self.port, self.baud)
        try:
            self._wait_ready(ready_timeout)
            self.info = self.hello()
        except Exception:
            self.close()
            raise
        self.agent_version = self.info.agent
        return self.info

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass  # already gone (unplugged); nothing left to release
        self._serial = None
        self._buffer = b""

    @property
    def connected(self) -> bool:
        return self._serial is not None

    def __enter__(self) -> "AgentClient":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── Commands ──

    def ping(self) -> PingResult:
        return PingResult.model_validate(self._request("PING"))

    def hello(self) -> HelloResult:
        return HelloResult.model_validate(self._request("HELLO"))

    def probe(self, pins: list[int] | None = None) -> ProbeResult:
        """Probe the given GPIOs, or every safe pin if `pins` is None."""
        return ProbeResult.model_validate(self._request("PROBE", *(pins or [])))

    def i2c(self) -> I2cResult:
        return I2cResult.model_validate(self._request("I2C", timeout=max(self.timeout, I2C_TIMEOUT)))

    def read(self, pin: int) -> ReadResult:
        return ReadResult.model_validate(self._request("READ", pin))

    # ── Wire protocol ──

    def _request(self, command: str, *args: object, timeout: float | None = None) -> dict[str, Any]:
        """Send a command and return its reply. Retries once on timeout, then raises AgentTimeout."""
        with self._lock:
            for _ in range(2):
                request_id = self._next_id
                self._next_id += 1
                line = " ".join([str(request_id), command, *map(str, args)])
                self._write(line + "\n")
                reply = self._read_reply(request_id, time.monotonic() + (timeout or self.timeout))
                if reply is None:
                    continue
                if not reply.get("ok"):
                    raise AgentCommandError(command, str(reply.get("error", "unknown error")))
                return reply
        raise AgentTimeout(f"no reply to {command} from the agent on {self.port}")

    def _read_reply(self, request_id: int, deadline: float) -> dict[str, Any] | None:
        while (line := self._readline(deadline)) is not None:
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # noise on the line
            if not isinstance(reply, dict):
                continue
            if reply.get("id") == request_id:
                return reply
            if reply.get("id") is None and reply.get("error") == "bad_request":
                # The agent couldn't even read the id, so this can only be our line.
                raise AgentCommandError("request", "bad_request")
            # Anything else is a late reply to an earlier attempt or a boot line: skip it.
        return None

    def _wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while (line := self._readline(deadline)) is not None:
            try:
                message = json.loads(line)
            except ValueError:
                continue  # the ROM bootloader's own output
            if isinstance(message, dict) and message.get("ready") is True:
                self.agent_version = message.get("agent")
                return True
        return False

    def _readline(self, deadline: float) -> str | None:
        """The next complete line, or None at the deadline. A partial line is kept for next time."""
        port = self._port()
        while True:
            if b"\n" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\n", 1)
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    return text
                continue
            if time.monotonic() >= deadline:
                return None
            try:
                self._buffer += port.readline()
            except Exception as e:
                self.close()
                raise AgentDisconnected(f"lost the agent on {self.port}: {e}") from e

    def _write(self, line: str) -> None:
        port = self._port()
        try:
            port.write(line.encode("ascii"))
        except Exception as e:
            self.close()
            raise AgentDisconnected(f"lost the agent on {self.port}: {e}") from e

    def _port(self) -> Port:
        if self._serial is None:
            raise AgentError("not connected to the agent")
        return self._serial
