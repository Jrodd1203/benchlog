import json
import threading

import pytest

from benchlog.serial.agent_client import (
    AgentClient,
    AgentCommandError,
    AgentDisconnected,
    AgentTimeout,
    PortBusy,
    open_serial,
)


class FakeAgent:
    """A fake serial port that answers like the firmware.

    `respond(line)` returns the raw bytes the "board" sends back; override it per test.
    Reads return at most one chunk per call, like a serial port with a short timeout.
    """

    def __init__(self, boot: bytes = b'{"ready":true,"agent":"0.1.0"}\r\n') -> None:
        self.incoming = [boot] if boot else []
        self.sent: list[str] = []
        self.closed = False
        self.unplugged = False

    def respond(self, line: str) -> bytes:
        request_id, command, *args = line.split()
        reply: dict = {"id": int(request_id), "ok": True, "cmd": command}
        if command == "PING":
            reply["uptime_ms"] = 1234
        elif command == "HELLO":
            reply |= {"board": "esp32", "agent": "0.1.0", "pins": [4, 18, 21]}
        elif command == "PROBE":
            pins = args or ["4", "18", "21"]
            reply["pins"] = {p: "unsafe" if p == "5" else "floating" for p in pins}
        elif command == "I2C":
            reply |= {"sda": 21, "scl": 22, "devices": ["0x76"]}
        elif command == "READ":
            if args[0] == "5":
                reply = {"id": int(request_id), "ok": False, "error": "unsafe_pin"}
            else:
                reply |= {"pin": int(args[0]), "digital": 1, "analog_mv": 3100}
        else:
            reply = {"id": int(request_id), "ok": False, "error": "unknown_command"}
        return (json.dumps(reply, separators=(",", ":")) + "\r\n").encode()

    # serial.Serial interface
    def write(self, data: bytes) -> int:
        if self.unplugged:
            raise OSError(6, "Device not configured")
        line = data.decode().rstrip("\n")
        self.sent.append(line)
        response = self.respond(line)
        if response:
            self.incoming.append(response)
        return len(data)

    def readline(self) -> bytes:
        if self.unplugged:
            raise OSError(6, "Device not configured")
        return self.incoming.pop(0) if self.incoming else b""

    def close(self) -> None:
        self.closed = True


def client_for(fake: FakeAgent, **kwargs) -> AgentClient:
    kwargs.setdefault("timeout", 0.2)
    return AgentClient("/dev/fake", opener=lambda port, baud: fake, **kwargs)


def test_connect_waits_for_ready_then_says_hello():
    fake = FakeAgent()
    with client_for(fake) as agent:
        assert agent.agent_version == "0.1.0"
        assert agent.info.pins == [4, 18, 21]
        assert fake.sent == ["1 HELLO"]
    assert fake.closed


def test_connect_skips_bootloader_noise():
    fake = FakeAgent(boot=b"\xff\xfe ets Jun  8 2016 00:22:57\r\nrst:0x1 (POWERON_RESET)\r\n")
    fake.incoming.append(b'{"ready":true,"agent":"0.1.0"}\n')
    with client_for(fake) as agent:
        assert agent.ping().uptime_ms == 1234


def test_connect_without_ready_line_still_works_if_hello_answers():
    # Some USB adapters don't reset the board when the port opens.
    fake = FakeAgent(boot=b"")
    agent = client_for(fake)
    agent.connect(ready_timeout=0.05)
    assert agent.info.board == "esp32"


def test_connect_fails_and_closes_when_nothing_answers():
    fake = FakeAgent(boot=b"")
    fake.respond = lambda line: b""
    agent = client_for(fake, timeout=0.05)
    with pytest.raises(AgentTimeout):
        agent.connect(ready_timeout=0.05)
    assert fake.closed and not agent.connected


def test_commands_return_typed_results():
    with client_for(FakeAgent()) as agent:
        probe = agent.probe([18, 5])
        assert probe.pins == {18: "floating", 5: "unsafe"}
        assert probe.taken_at.endswith("+00:00")
        assert agent.probe().pins == {4: "floating", 18: "floating", 21: "floating"}
        assert agent.i2c().devices == ["0x76"]
        read = agent.read(34)
        assert (read.pin, read.digital, read.analog_mv) == (34, 1, 3100)


def test_ids_increment_and_args_are_sent():
    fake = FakeAgent()
    with client_for(fake) as agent:
        agent.ping()
        agent.probe([18, 21])
        agent.read(34)
    assert fake.sent == ["1 HELLO", "2 PING", "3 PROBE 18 21", "4 READ 34"]


def test_skips_garbage_and_stale_replies():
    fake = FakeAgent()
    normal = fake.respond

    def noisy(line: str) -> bytes:
        request_id = int(line.split()[0])
        stale = json.dumps({"id": request_id - 1, "ok": True, "cmd": "PING", "uptime_ms": 1}).encode()
        return b"\xff\xfe\xfd garbage\r\n[1,2,3]\n\n" + stale + b"\n" + normal(line)

    fake.respond = noisy
    with client_for(fake) as agent:
        assert agent.ping().uptime_ms == 1234


def test_reply_split_across_reads():
    fake = FakeAgent()
    normal = fake.respond

    def split(line: str) -> bytes:
        data = normal(line)
        fake.incoming.append(data[:10])
        return data[10:]

    fake.respond = split
    with client_for(fake) as agent:
        assert agent.ping().uptime_ms == 1234


def test_timeout_retries_once_with_a_new_id():
    fake = FakeAgent()
    normal = fake.respond
    dropped = []

    def drop_first_ping(line: str) -> bytes:
        if "PING" in line and not dropped:
            dropped.append(line)
            return b""
        return normal(line)

    fake.respond = drop_first_ping
    with client_for(fake) as agent:
        assert agent.ping().uptime_ms == 1234
    assert fake.sent[1:] == ["2 PING", "3 PING"]


def test_timeout_twice_raises():
    fake = FakeAgent()
    normal = fake.respond
    fake.respond = lambda line: b"" if "PING" in line else normal(line)
    with client_for(fake, timeout=0.05) as agent:
        with pytest.raises(AgentTimeout):
            agent.ping()
    assert fake.sent[1:] == ["2 PING", "3 PING"]


def test_error_reply_raises_with_code():
    with client_for(FakeAgent()) as agent:
        with pytest.raises(AgentCommandError) as e:
            agent.read(5)
        assert e.value.code == "unsafe_pin"
        assert agent.ping().uptime_ms == 1234  # still usable afterwards


def test_unplugged_raises_disconnected():
    fake = FakeAgent()
    agent = client_for(fake)
    agent.connect()
    fake.unplugged = True
    with pytest.raises(AgentDisconnected):
        agent.ping()
    assert not agent.connected


class SlowAgent(FakeAgent):
    """Replies only after a few empty reads, and notes if a second request arrives meanwhile."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = 0
        self.overlaps = 0

    def write(self, data: bytes) -> int:
        if self.waiting:
            self.overlaps += 1
        self.waiting = 3
        return super().write(data)

    def readline(self) -> bytes:
        if self.waiting > 1:
            self.waiting -= 1
            threading.Event().wait(0.005)  # give other threads a chance to jump in
            return b""
        self.waiting = 0
        return super().readline()


def test_one_request_at_a_time():
    fake = SlowAgent()
    with client_for(fake) as agent:
        threads = [threading.Thread(target=agent.ping) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert len(fake.sent) == 9
    assert fake.overlaps == 0


def test_busy_port_has_a_clear_error(monkeypatch: pytest.MonkeyPatch):
    serial = pytest.importorskip("serial")

    def busy(*args, **kwargs):
        raise serial.SerialException("[Errno 16] could not open port /dev/cu.usbserial: Resource busy")

    monkeypatch.setattr(serial, "Serial", busy)
    with pytest.raises(PortBusy, match="another process is using the port /dev/cu.usbserial"):
        open_serial("/dev/cu.usbserial", 115200)
