"""Local FastAPI service used by the web UI. Endpoints are agreed at kickoff."""

import math
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="benchlog")

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/scan")
async def scan(file: UploadFile = File(...)):
    data = await file.read()
    arr = np.frombuffer(data, np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return {"present": False, "occupied": [], "error": "could not decode image"}
    from benchlog.vision.capture import analyse_image
    result = analyse_image(frame)
    return {
        "present": result.present,
        "empty": result.empty,
        "occupied": result.occupied,
        "residual_px": None if math.isinf(result.residual_px) else result.residual_px,
        "upside_down": result.upside_down,
        "warnings": result.warnings,
    }
