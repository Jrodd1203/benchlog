"""API for the ESP32 serial agent.

The app keeps one `SerialService` on `app.state.serial` (created at startup, closed at shutdown).
If the router is used without that startup hook, the service is created on first use instead.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from benchlog.serial.agent_client import AgentError
from benchlog.serial.service import PortInfo, SerialService, SerialSnapshot, SerialStatus, available_ports

router = APIRouter(prefix="/api/serial", tags=["serial"])


def serial_service(app: FastAPI) -> SerialService:
    """The app's SerialService, created on first use."""
    if getattr(app.state, "serial", None) is None:
        app.state.serial = SerialService()
    return app.state.serial


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
def status(service: SerialDep) -> SerialStatus:
    return service.status()


@router.post("/probe")
def probe(service: SerialDep) -> SerialSnapshot:
    """Take the same snapshot a scan would (for debugging)."""
    snapshot = service.snapshot()
    if snapshot is None:
        raise HTTPException(status_code=503, detail=service.status().last_error or "serial agent not connected")
    return snapshot
