"""Calibrate-and-lock: hole map snapping, tracking board moves, per-hole classification."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from typer.testing import CliRunner  # noqa: E402

from benchlog.cli.main import app  # noqa: E402
from benchlog.core.project import Project  # noqa: E402
from benchlog.core.repo import Repo  # noqa: E402
from benchlog.vision import calibration as cal_mod  # noqa: E402
from benchlog.vision import camera  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DESK_W, DESK_H, DESK_GRAY = 1600, 900, 35


def desk_frame() -> np.ndarray:
    """The white-board photo, cropped to the board, on a dark desk with room to move."""
    board = cv2.imread(str(FIXTURES / "bb830_white_topdown.jpg"))[74:437, 62:1118]
    desk = np.full((DESK_H, DESK_W, 3), DESK_GRAY, np.uint8)
    y, x = (DESK_H - board.shape[0]) // 2, (DESK_W - board.shape[1]) // 2
    desk[y : y + board.shape[0], x : x + board.shape[1]] = board
    return desk


DESK = desk_frame()


@pytest.fixture(scope="module")
def cal() -> cal_mod.Calibration:
    editor = cal_mod.CalibrationEditor()
    editor.update(DESK, now=0.0)
    assert editor.locked
    return cal_mod.Calibration(corners=editor.corners, holes=editor.holes, reference=DESK.copy())


def moved(frame: np.ndarray, degrees: float = 2.0, dx: float = 37, dy: float = -21) -> tuple[np.ndarray, np.ndarray]:
    matrix = cv2.getRotationMatrix2D((DESK_W / 2, DESK_H / 2), degrees, 1.0)
    matrix[:, 2] += (dx, dy)
    return cv2.warpAffine(frame, matrix, (DESK_W, DESK_H), borderValue=(DESK_GRAY,) * 3), matrix


def plug(frame: np.ndarray, holes: dict, names_colors: dict[str, tuple[int, int, int]]) -> np.ndarray:
    """Draw a wire end (a colored dot about a hole wide) at each named hole."""
    out = frame.copy()
    radius = round(cal_mod.hole_pitch_px(holes) * 0.4)
    for name, color in names_colors.items():
        x, y = holes[name]
        cv2.circle(out, (round(x), round(y)), radius, color, -1)
    return out


def gray_at(frame: np.ndarray, point: tuple[float, float]) -> float:
    x, y = round(point[0]), round(point[1])
    return float(cv2.cvtColor(frame[y - 1 : y + 2, x - 1 : x + 2], cv2.COLOR_BGR2GRAY).mean())


# ── Hole map ──────────────────────────────────────────────────────────────────


def test_auto_calibration_snaps_dots_into_holes(cal: cal_mod.Calibration) -> None:
    plastic = gray_at(DESK, np.mean([cal.holes["A30"], cal.holes["A31"]], axis=0))
    for name in ("A1", "A63", "E1", "F1", "J1", "J63", "L+1", "L-50", "R+1", "R-50"):
        assert gray_at(DESK, cal.holes[name]) < plastic - 80, name


def test_layout_alone_is_off_but_snapping_fixes_it(cal: cal_mod.Calibration) -> None:
    nominal = cal_mod.holes_from_corners(cal.corners)
    offsets = [np.linalg.norm(np.subtract(nominal[n], cal.holes[n])) for n in cal.holes]
    assert np.median(offsets) > 3  # the measured layout alone sits between holes


def test_dragging_a_corner_goes_manual_then_snaps_back_on(cal: cal_mod.Calibration) -> None:
    editor = cal_mod.CalibrationEditor(corners=cal.corners.copy(), holes=dict(cal.holes), locked=True)
    x, y = editor.corners[2]
    editor.on_mouse(cv2.EVENT_LBUTTONDOWN, round(x) + 5, round(y) + 5, (DESK_W, DESK_H))
    assert editor.dragging and editor.selected == 2 and not editor.auto
    editor.on_mouse(cv2.EVENT_MOUSEMOVE, round(x) + 4, round(y) + 3, (DESK_W, DESK_H))
    assert not editor.locked and editor.needs_snap
    editor.update(DESK, now=1.0)
    assert not editor.locked  # still dragging: don't fight the user
    editor.on_mouse(cv2.EVENT_LBUTTONUP, round(x) + 4, round(y) + 3, (DESK_W, DESK_H))
    editor.update(DESK, now=1.1)
    assert editor.locked
    assert np.linalg.norm(np.subtract(editor.holes["J1"], cal.holes["J1"])) < 1.5


def test_keys(cal: cal_mod.Calibration) -> None:
    editor = cal_mod.CalibrationEditor()
    assert editor.on_key(13, DESK, 0.0) is None and "no hole map" in editor.message
    editor = cal_mod.CalibrationEditor(corners=cal.corners.copy(), holes=dict(cal.holes))
    editor.on_key(ord("3"), DESK, 0.0)
    editor.on_key(ord("l"), DESK, 0.0)  # nudge corner 3 right by 1 px
    assert editor.corners[2][0] == pytest.approx(cal.corners[2][0] + 1)
    assert not editor.auto and editor.needs_snap
    editor.on_key(ord("a"), DESK, 0.0)
    assert editor.auto
    assert editor.on_key(13, DESK, 0.0) == "save"
    assert editor.on_key(27, DESK, 0.0) == "quit"


def test_unsnappable_board_falls_back_to_layout_with_a_hint() -> None:
    blank = np.full_like(DESK, DESK_GRAY)
    cv2.rectangle(blank, (200, 300), (1400, 600), (220, 220, 220), -1)  # a board with no holes
    editor = cal_mod.CalibrationEditor(corners=np.float32([[200, 300], [1400, 300], [1400, 600], [200, 600]]), auto=False)
    editor.snap(blank, now=0.0)
    assert not editor.locked and editor.holes is not None
    assert "corner handles" in editor.message


def test_save_and_load(cal: cal_mod.Calibration, tmp_path: Path) -> None:
    saved = cal_mod.Calibration(cal.corners, cal.holes, cal.reference, camera=1, occupied=["A4", "F21"])
    saved.save(tmp_path)
    loaded = cal_mod.Calibration.load(tmp_path)
    assert loaded.camera == 1 and loaded.occupied == ["A4", "F21"]
    assert loaded.frame_size == (DESK_W, DESK_H)
    assert np.linalg.norm(np.subtract(loaded.holes["J63"], cal.holes["J63"])) < 0.01
    assert cal_mod.Calibration.load(tmp_path / "missing") is None


# ── Tracking ──────────────────────────────────────────────────────────────────


def test_tracks_a_bumped_board(cal: cal_mod.Calibration) -> None:
    frame, matrix = moved(DESK)
    tracking = cal_mod.track(cal, frame)
    got = tracking.apply(cal.holes)
    for name, (x, y) in cal.holes.items():
        expected = matrix @ np.array([x, y, 1.0])
        assert np.linalg.norm(np.subtract(got[name], expected)) < 0.5, name
    assert tracking.rotation_deg == pytest.approx(-2.0, abs=0.05)


def test_lost_board_and_resolution_change(cal: cal_mod.Calibration) -> None:
    with pytest.raises(cal_mod.CalibrationError):
        cal_mod.track(cal, np.full_like(DESK, DESK_GRAY))
    with pytest.raises(cal_mod.CalibrationError, match="resolution changed"):
        cal_mod.track(cal, cv2.resize(DESK, (1280, 720)))


# ── Classification ────────────────────────────────────────────────────────────


def test_unchanged_board_has_no_occupied_holes(cal: cal_mod.Calibration) -> None:
    frame, _ = moved(DESK)
    assert cal_mod.classify(cal, frame, cal_mod.track(cal, frame)).occupied == []


def test_finds_wires_despite_movement_and_dimmer_light(cal: cal_mod.Calibration) -> None:
    frame, matrix = moved(DESK)
    holes_now = cal_mod.Tracking(matrix=matrix.astype(np.float32), correlation=1.0).apply(cal.holes)
    wires = {"A4": (0, 220, 255), "A12": (0, 220, 255), "F21": (0, 220, 255), "J30": (0, 0, 200), "R-15": (20, 20, 20)}
    frame = cv2.convertScaleAbs(plug(frame, holes_now, wires), alpha=0.8, beta=10)
    occupancy = cal_mod.classify(cal, frame, cal_mod.track(cal, frame))
    assert sorted(occupancy.occupied) == sorted(wires)


def test_calibrated_with_circuit_reports_removals(cal: cal_mod.Calibration) -> None:
    built = plug(DESK, cal.holes, {"A4": (0, 220, 255), "F21": (0, 220, 255)})
    with_circuit = cal_mod.Calibration(cal.corners, cal.holes, built, occupied=["A4", "F21"])
    # The A4 end moves to A12: A4 now looks like bare board, A12 like a wire.
    after = plug(DESK, cal.holes, {"A12": (0, 220, 255), "F21": (0, 220, 255)})
    occupancy = cal_mod.classify(with_circuit, after, cal_mod.track(with_circuit, after))
    assert sorted(occupancy.changed) == ["A12", "A4"]
    assert sorted(occupancy.occupied) == ["A12", "F21"]


# ── Scanning with a calibration ───────────────────────────────────────────────


def test_scan_uses_the_calibration(cal: cal_mod.Calibration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    cal.save(project.calibration_dir)

    bumped, matrix = moved(DESK, degrees=1.0, dx=-15, dy=9)
    holes_now = cal_mod.Tracking(matrix=matrix.astype(np.float32), correlation=1.0).apply(cal.holes)
    frame = plug(bumped, holes_now, {"A40": (0, 200, 0), "A45": (0, 200, 0)})
    class FakeCapture:
        def release(self) -> None:
            pass

    grabbed_with = {}

    def fake_grab(cap, timeout, board_corners=None):
        grabbed_with["corners"] = board_corners
        return frame

    monkeypatch.setattr(camera, "open_camera", lambda index: FakeCapture())
    monkeypatch.setattr(camera, "grab_steady_frame", fake_grab)

    result = CliRunner(env={"COLUMNS": "200"}).invoke(app, ["scan"])
    assert result.exit_code == 0, result.output
    assert "added wire" in result.output and "A40, A45" in result.output
    assert "no calibration saved" not in result.output
    # Stillness was judged on the calibrated board area, not a fresh (wobbly) detection.
    assert np.allclose(grabbed_with["corners"], cal.corners)


@pytest.mark.parametrize("degrees, dx, dy", [(1.5, 25, -12), (-3.0, -40, 30)])
def test_clear_board_tracks_and_classifies(degrees: float, dx: float, dy: float) -> None:
    """The translucent board: holes can't be snapped automatically, but once placed (here from
    the detected corners) tracking and per-hole comparison work the same."""
    board = cv2.imread(str(FIXTURES / "bb830_clear_topdown.jpg"))[90:468, 50:1134]
    desk = np.full((DESK_H, DESK_W, 3), DESK_GRAY, np.uint8)
    desk[250 : 250 + board.shape[0], 250 : 250 + board.shape[1]] = board
    editor = cal_mod.CalibrationEditor()
    editor.update(desk, now=0.0)
    assert not editor.locked and "corner handles" in editor.message
    clear = cal_mod.Calibration(corners=editor.corners, holes=editor.holes, reference=desk)

    frame, _ = moved(desk, degrees, dx, dy)
    tracking = cal_mod.track(clear, frame)
    assert cal_mod.classify(clear, frame, tracking).occupied == []
    wires = {"A4": (0, 200, 255), "A12": (0, 200, 255), "F21": (0, 0, 200), "R-15": (25, 25, 25)}
    frame = cv2.convertScaleAbs(plug(frame, tracking.apply(clear.holes), wires), alpha=0.85, beta=15)
    assert sorted(cal_mod.classify(clear, frame, cal_mod.track(clear, frame)).occupied) == sorted(wires)


# ── Wobbly outline detection ──────────────────────────────────────────────────


def wobbly_detector(monkeypatch: pytest.MonkeyPatch, module) -> None:
    """Replace board detection with one that jumps around by up to 12 px every call."""
    rng = np.random.default_rng(0)
    real = module.detect_board_downscaled

    def wobbly(frame, prefer_near=None):
        corners = real(frame)
        return None if corners is None else corners + rng.uniform(-12, 12, corners.shape).astype(np.float32)

    monkeypatch.setattr(module, "detect_board_downscaled", wobbly)


def test_locked_grid_stops_following_a_wobbly_detector(cal: cal_mod.Calibration, monkeypatch: pytest.MonkeyPatch) -> None:
    editor = cal_mod.CalibrationEditor()
    editor.update(DESK, now=0.0)
    assert editor.locked
    locked_holes = dict(editor.holes)
    wobbly_detector(monkeypatch, cal_mod)
    for i in range(1, 20):
        editor.update(DESK, now=i * 0.1)
    assert editor.holes == locked_holes
    editor.on_key(ord("a"), DESK, now=3.0)  # re-detect on request
    assert editor.auto and not editor.locked


def test_tracking_ignores_a_wobbly_detector(cal: cal_mod.Calibration, monkeypatch: pytest.MonkeyPatch) -> None:
    wobbly_detector(monkeypatch, cal_mod)
    for degrees, dx, dy in [(0.0, 0, 0), (0.7, 6, -4)]:
        frame, matrix = moved(DESK, degrees, dx, dy)
        got = cal_mod.track(cal, frame).apply(cal.holes)
        expected = matrix @ np.array([*cal.holes["J63"], 1.0])
        assert np.linalg.norm(np.subtract(got["J63"], expected)) < 0.5


def test_calibrated_scan_is_not_blocked_by_a_wobbly_detector(cal: cal_mod.Calibration, monkeypatch: pytest.MonkeyPatch) -> None:
    wobbly_detector(monkeypatch, camera)

    class StillCamera:
        def read(self):
            return True, DESK.copy()

    clock = iter(np.arange(0, 100, 1 / 30))
    frame = camera.grab_steady_frame(StillCamera(), timeout=5, clock=lambda: next(clock), board_corners=cal.corners)
    assert frame.shape == DESK.shape
    # Without the calibrated area, the same wobble never counts as steady.
    clock = iter(np.arange(0, 100, 1 / 30))
    with pytest.raises(camera.CameraError, match="hold still"):
        camera.grab_steady_frame(StillCamera(), timeout=5, clock=lambda: next(clock))


def test_scan_debug_explains_what_the_camera_saw(cal: cal_mod.Calibration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    project, _ = Project.init(path)
    cal.save(project.calibration_dir)
    photo = tmp_path / "wire.png"
    cv2.imwrite(str(photo), plug(DESK, cal.holes, {"A40": (0, 200, 0), "A45": (0, 200, 0)}))

    result = CliRunner(env={"COLUMNS": "300"}).invoke(app, ["scan", "--image", str(photo), "--debug"])
    assert result.exit_code == 0, result.output
    assert "most changed: A40" in result.output or "most changed: A45" in result.output
    assert "so filled: ['A40', 'A45']" in result.output
    assert (project.scans.dir / "debug" / "changes.png").exists()
    assert (project.scans.dir / "debug" / "frame.png").exists()


def test_saving_warns_when_the_board_isnt_empty(cal: cal_mod.Calibration) -> None:
    with_wire = plug(DESK, cal.holes, {"C10": (0, 160, 0), "C11": (0, 160, 0), "C12": (0, 160, 0)})
    editor = cal_mod.CalibrationEditor(corners=cal.corners.copy(), holes=dict(cal.holes), locked=True, auto=False)
    assert editor.on_key(13, with_wire, 0.0) is None
    assert "doesn't look empty" in editor.message and "C10, C11, C12" in editor.message
    assert editor.on_key(-1, with_wire, 0.1) is None  # frames with no key pressed don't cancel it
    assert editor.on_key(13, with_wire, 0.2) == "save"  # Enter again: save anyway

    empty = cal_mod.CalibrationEditor(corners=cal.corners.copy(), holes=dict(cal.holes), locked=True, auto=False)
    assert empty.on_key(13, DESK, 0.0) == "save"
    built = cal_mod.CalibrationEditor(corners=cal.corners.copy(), holes=dict(cal.holes), expect_empty=False)
    assert built.on_key(13, with_wire, 0.0) == "save"  # --board-matches-circuit: not meant to be empty


def test_row_numbering_flip(cal: cal_mod.Calibration) -> None:
    editor = cal_mod.CalibrationEditor(corners=cal.corners.copy(), holes=dict(cal.holes))
    editor.on_key(ord("r"), DESK, 0.0)
    flipped = editor.named_holes()
    assert flipped["A1"] == cal.holes["A63"] and flipped["J63"] == cal.holes["J1"]
    assert flipped["L+1"] == cal.holes["L+50"] and flipped["R-50"] == cal.holes["R-1"]
