"""API for the ESP32 serial agent.

The app keeps one `SerialService` on `app.state.serial` (created at startup, closed at shutdown).
If the router is used without that startup hook, the service is created on first use instead.

Scans and commits call `snapshot_for()`, which connects on its own (to the port saved with
`benchlog serial use`, else an auto-detected ESP32) when nobody has connected yet, like the CLI.
"""

import os
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from benchlog.core.project import Project, ProjectError
from benchlog.core.reconcile import SerialReadings
from benchlog.serial import service as service_module
from benchlog.serial.agent_client import AgentError, PinState
from benchlog.serial.service import PortInfo, SerialService, SerialSnapshot, SerialStatus, available_ports
from benchlog.server.deps import get_project

# After a failed automatic connect, wait this long before trying that port again, so a busy or
# silent port doesn't add the connect timeout to every scan.
RETRY_AFTER = 30.0

router = APIRouter(prefix="/api/serial", tags=["serial"])


def serial_service(app: FastAPI) -> SerialService:
    """The app's SerialService, created on first use."""
    if getattr(app.state, "serial", None) is None:
        app.state.serial = SerialService()
    return app.state.serial


def server_serial_enabled() -> bool:
    """False with BENCHLOG_SERVER_SERIAL=off: the server never connects to an ESP32 on its own, so the
    browser can own the USB port (Web Serial). Useful locally, and on a hosted server with no USB."""
    return os.environ.get("BENCHLOG_SERVER_SERIAL", "on").strip().lower() not in ("0", "off", "false", "no")


_connecting = threading.Lock()  # one automatic connect at a time (a connect reboots the ESP32)


def ensure_connected(app: FastAPI, project: Project | None) -> None:
    """Connect to the ESP32 if nobody has yet: the port saved with `benchlog serial use`, else an
    auto-detected one. Never raises; a failure shows up in status() and isn't retried for a while."""
    if not server_serial_enabled():
        return  # the browser owns the ESP32 (Web Serial); see server_serial_enabled()
    service = serial_service(app)
    if service.status().connected or not _connecting.acquire(blocking=False):
        return  # connected, or another request is connecting right now
    try:
        saved = project.config().get("serial_port") if project is not None else None
        port = saved or service_module.find_esp32_port()
        failed = getattr(app.state, "serial_failed", {})
        if port and time.monotonic() - failed.get(port, float("-inf")) >= RETRY_AFTER:
            try:
                service.connect(port)
            except AgentError:
                app.state.serial_failed = {**failed, port: time.monotonic()}  # the reason is in status()
    finally:
        _connecting.release()


def snapshot_for(app: FastAPI, project: Project) -> SerialSnapshot | None:
    """Probe the ESP32 for a scan or commit, connecting first if needed. None if there's no ESP32."""
    ensure_connected(app, project)
    return serial_service(app).snapshot()


class ClientSerialReadings(SerialReadings):
    """Readings a client took itself (the browser over Web Serial), validated strictly."""

    pins: dict[int, PinState]


CLIENT_SERIAL_FIELD = (
    "ESP32 readings the browser took over Web Serial (a hosted server can't reach the USB port). "
    "Without them, the server's own ESP32 is used if it has one (local `benchlog serve`)."
)


def readings_for(app: FastAPI, project: Project, client: SerialReadings | None) -> SerialReadings | None:
    """What the ESP32 senses, for a scan, commit or check: the browser's readings if it sent them,
    else the server's own ESP32 (connecting if needed), else None (camera only / check skipped)."""
    if client is not None:
        return client
    snapshot = snapshot_for(app, project)
    return snapshot.readings() if snapshot else None


def _optional_project(project: str | None = None) -> Project | None:
    """The request's project if there is one; the status light must work even outside a project."""
    try:
        return get_project(project)
    except ProjectError:
        return None


def get_serial(request: Request) -> SerialService:
    return serial_service(request.app)


SerialDep = Annotated[SerialService, Depends(get_serial)]


class ConnectRequest(BaseModel):
    port: str


@router.get("/ports")
def ports() -> list[PortInfo]:
    try:
        return available_ports()
    except AgentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/connect")
def connect(request: ConnectRequest, service: SerialDep) -> SerialStatus:
    """Open the port and say HELLO. Takes a few seconds: the ESP32 reboots when the port opens."""
    try:
        return service.connect(request.port)
    except AgentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/status")
def status(
    request: Request, service: SerialDep, project: Annotated[Project | None, Depends(_optional_project)]
) -> SerialStatus:
    """Connection status. Connects first if an ESP32 is plugged in and nobody has connected yet,
    so the status light is right without pressing Connect."""
    ensure_connected(request.app, project)
    return service.status()


@router.post("/probe")
def probe(service: SerialDep) -> SerialSnapshot:
    """Take the same snapshot a scan would (for debugging)."""
    snapshot = service.snapshot()
    if snapshot is None:
        raise HTTPException(status_code=503, detail=service.status().last_error or "serial agent not connected")
    return snapshot
