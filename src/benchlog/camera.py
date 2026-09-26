"""Read the physical board through the vision pipeline. Shared by the CLI and the API.

With a saved calibration (`benchlog camera calibrate`), the board is tracked from its calibrated
hole map and each hole is compared with its empty-board appearance. Without one, the hole map is
rebuilt from the image on every scan (less reliable).

OpenCV is imported lazily so the rest of benchlog works without the `vision` extra.
"""

from pathlib import Path

from benchlog.core.project import ProjectError
from benchlog.core.scan import BoardReading

_NEEDS_VISION = (
    "camera scanning needs the vision pipeline (install with `pip install -e '.[vision]'`); "
    "use --simulate CIRCUIT.json until then"
)


def load_calibration(directory: Path | None):
    """The saved calibration, or None if there isn't one."""
    if directory is None:
        return None
    try:
        from benchlog.vision.calibration import Calibration, CalibrationError
    except ImportError as e:
        raise ProjectError(_NEEDS_VISION) from e
    try:
        return Calibration.load(directory)
    except CalibrationError as e:
        raise ProjectError(str(e)) from e


def reading_from_frame(frame, source: str, calibration=None) -> BoardReading:
    """Hole occupancy of one frame (a numpy BGR image)."""
    if calibration is not None:
        from benchlog.vision.calibration import CalibrationError, classify, track

        try:
            tracking = track(calibration, frame)
        except CalibrationError as e:
            raise ProjectError(str(e)) from e
        occupancy = classify(calibration, frame, tracking)
        warnings = []
        if tracking.shift_px > 3:
            warnings.append(f"board moved {tracking.shift_px:.0f} px / {tracking.rotation_deg:.1f}° since calibration (tracked)")
        return BoardReading(
            occupied=sorted(occupancy.occupied),
            confidence=round(min(1.0, tracking.correlation), 3),
            warnings=warnings,
            source=source,
        )

    try:
        from benchlog.vision.capture import analyse_image
    except ImportError as e:
        raise ProjectError(_NEEDS_VISION) from e

    result = analyse_image(frame)
    if not result.present:
        raise ProjectError("; ".join(result.warnings) or "no breadboard found")
    if result.upside_down:
        raise ProjectError("the board is upside down; rotate it so row 1 is on the right and scan again")
    warnings = result.warnings + ["no calibration saved; run `benchlog camera calibrate` for reliable scans"]
    return BoardReading(
        occupied=result.occupied,
        confidence=0.6 if result.warnings else 1.0,
        warnings=warnings,
        source=source,
    )


def read_board(camera: int, image: Path | None, calibration_dir: Path | None = None, timeout: float = 10.0) -> BoardReading:
    """Read the board from a photo, or from the camera once the board is in view and still."""
    try:
        import cv2

        from benchlog.vision.camera import CameraError, grab_steady_frame, open_camera
    except ImportError as e:
        raise ProjectError(_NEEDS_VISION) from e
    calibration = load_calibration(calibration_dir)

    if image is not None:
        frame = cv2.imread(str(image))
        if frame is None:
            raise ProjectError(f"can't read image {image}")
        return reading_from_frame(frame, f"image {image.name}", calibration)

    try:
        cap = open_camera(camera)
        try:
            corners = calibration.corners if calibration is not None else None
            frame = grab_steady_frame(cap, timeout=timeout, board_corners=corners)
        finally:
            cap.release()
    except CameraError as e:
        raise ProjectError(str(e)) from e
    return reading_from_frame(frame, f"camera {camera}", calibration)
