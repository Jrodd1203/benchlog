"""Read the physical board through the vision pipeline. Shared by the CLI and the API.

OpenCV is imported lazily so the rest of benchlog works without the `vision` extra.
"""

from pathlib import Path

from benchlog.core.project import ProjectError
from benchlog.core.scan import BoardReading


def read_board(camera: int, image: Path | None) -> BoardReading:
    """Read hole occupancy from a photo or the camera using the vision pipeline."""
    try:
        import cv2

        from benchlog.vision.capture import analyse_image
    except ImportError as e:
        raise ProjectError(
            "camera scanning needs the vision pipeline (install with `pip install -e '.[vision]'`); "
            "use --simulate CIRCUIT.json until then"
        ) from e

    if image is not None:
        frame = cv2.imread(str(image))
        if frame is None:
            raise ProjectError(f"can't read image {image}")
        source = f"image {image.name}"
    else:
        cap = cv2.VideoCapture(camera)
        try:
            frame = None
            for _ in range(10):  # let exposure settle before keeping a frame
                ok, grabbed = cap.read()
                frame = grabbed if ok else frame
        finally:
            cap.release()
        if frame is None:
            raise ProjectError(f"can't read from camera {camera}")
        source = f"camera {camera}"

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

