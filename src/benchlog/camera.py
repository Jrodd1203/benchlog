"""Read the physical board through the vision pipeline. Shared by the CLI and the API.

OpenCV is imported lazily so the rest of benchlog works without the `vision` extra.
"""

from pathlib import Path

from benchlog.core.project import ProjectError
from benchlog.core.scan import BoardReading

_NEEDS_VISION = (
    "camera scanning needs the vision pipeline (install with `pip install -e '.[vision]'`); "
    "use --simulate CIRCUIT.json until then"
)


def reading_from_frame(frame, source: str) -> BoardReading:
    """Hole occupancy of one frame (a numpy BGR image)."""
    try:
        from benchlog.vision.capture import analyse_image
    except ImportError as e:
        raise ProjectError(_NEEDS_VISION) from e

    result = analyse_image(frame)
    if not result.present:
        raise ProjectError("; ".join(result.warnings) or "no breadboard found")
    if result.upside_down:
        raise ProjectError("the board is upside down; rotate it so row 1 is on the right and scan again")
    return BoardReading(
        occupied=result.occupied,
        confidence=0.6 if result.warnings else 1.0,
        warnings=result.warnings,
        source=source,
    )


def read_board(camera: int, image: Path | None, timeout: float = 10.0) -> BoardReading:
    """Read the board from a photo, or from the camera once the board is in view and still."""
    try:
        import cv2

        from benchlog.vision.camera import CameraError, grab_steady_frame, open_camera
    except ImportError as e:
        raise ProjectError(_NEEDS_VISION) from e

    if image is not None:
        frame = cv2.imread(str(image))
        if frame is None:
            raise ProjectError(f"can't read image {image}")
        return reading_from_frame(frame, f"image {image.name}")

    try:
        cap = open_camera(camera)
        try:
            frame = grab_steady_frame(cap, timeout=timeout)
        finally:
            cap.release()
    except CameraError as e:
        raise ProjectError(str(e)) from e
    return reading_from_frame(frame, f"camera {camera}")
