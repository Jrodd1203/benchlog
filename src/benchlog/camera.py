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
        from benchlog.vision.calibration import PATCH_RADIUS_PITCH, hole_pitch_px
        from benchlog.vision.colors import hole_colors

        radius = max(2, round(PATCH_RADIUS_PITCH * hole_pitch_px(calibration.holes)))
        colors = hole_colors(frame, tracking.apply(calibration.holes), occupancy.occupied, radius)
        from benchlog.vision.calibration import change_map
        from benchlog.vision.objects import find_objects
        from benchlog.vision.parts import recognize

        _, aligned = change_map(calibration, frame, tracking)
        parts = recognize(calibration, find_objects(calibration, frame, tracking), aligned)
        warnings = []
        if tracking.shift_px > 3:
            warnings.append(f"board moved {tracking.shift_px:.0f} px / {tracking.rotation_deg:.1f}° since calibration (tracked)")
        return BoardReading(
            occupied=sorted(occupancy.occupied),
            colors=colors,
            parts=parts,
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


def write_debug(frame, calibration, debug_dir: Path) -> list[str]:
    """Save the frame and a per-hole change map to `debug_dir`; returns lines describing the scan."""
    import cv2

    debug_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(debug_dir / "frame.png"), frame)
    if calibration is None:
        return [f"saved the frame to {debug_dir} (no calibration, so no change map)"]
    from benchlog.vision.calibration import CalibrationError, classify, debug_image, track

    try:
        tracking = track(calibration, frame)
    except CalibrationError as e:
        return [f"tracking failed: {e}", f"saved the frame to {debug_dir}"]
    occupancy = classify(calibration, frame, tracking)
    cv2.imwrite(str(debug_dir / "changes.png"), debug_image(calibration, frame, occupancy))
    from benchlog.vision.calibration import change_map
    from benchlog.vision.objects import draw_objects, find_objects

    objects = find_objects(calibration, frame, tracking)
    _, aligned = change_map(calibration, frame, tracking)
    cv2.imwrite(str(debug_dir / "objects.png"), draw_objects(aligned, objects))
    ranked = sorted(occupancy.diffs, key=occupancy.diffs.get, reverse=True)
    values = sorted(occupancy.diffs.values())
    return [
        f"tracking: match {tracking.correlation:.3f}, moved {tracking.shift_px:.1f} px, rotated {tracking.rotation_deg:.2f}°",
        f"change threshold {occupancy.threshold:.1f} (typical hole {values[len(values) // 2]:.1f})",
        "most changed: " + ", ".join(f"{n} {occupancy.diffs[n]:.1f}" for n in ranked[:10]),
        f"changed since calibration: {sorted(occupancy.changed) or 'none'}",
        f"objects since calibration: {len(objects) or 'none'}",
        *(f"  {obj.summary()}" for obj in objects),
        f"saved frame.png, changes.png and objects.png to {debug_dir}",
    ]


def read_board(
    camera: int,
    image: Path | None,
    calibration_dir: Path | None = None,
    timeout: float = 10.0,
    debug_dir: Path | None = None,
    debug_lines: list[str] | None = None,
) -> BoardReading:
    """Read the board from a photo, or from the camera once the board is in view and still.

    With `debug_dir`, also saves the frame and a change map there and appends a description of the
    scan to `debug_lines`.
    """
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
        source = f"image {image.name}"
    else:
        try:
            cap = open_camera(camera)
            try:
                corners = calibration.corners if calibration is not None else None
                frame = grab_steady_frame(cap, timeout=timeout, board_corners=corners)
            finally:
                cap.release()
        except CameraError as e:
            raise ProjectError(str(e)) from e
        source = f"camera {camera}"
    if debug_dir is not None:
        lines = write_debug(frame, calibration, debug_dir)
        if debug_lines is not None:
            debug_lines.extend(lines)
    return reading_from_frame(frame, source, calibration)

