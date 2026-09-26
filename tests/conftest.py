import pytest


@pytest.fixture(autouse=True)
def _no_real_serial_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never auto-detect (and so never talk to) an ESP32 that happens to be plugged in."""
    from benchlog.serial import service

    monkeypatch.setattr(service, "find_esp32_port", lambda: None)
