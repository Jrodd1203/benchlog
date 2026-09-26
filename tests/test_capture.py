"""Tests for BB830 baseline capture. Owner: Person 4.

Runs only when OpenCV is installed (the `vision` extra) -- CI's core+dev
install must still pass without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from benchlog.core.board import BB830  # noqa: E402
from benchlog.vision import capture as cap  # noqa: E402
from benchlog.vision.bb830_layout import BOARD_H_MM, BOARD_W_MM, hole_positions_mm  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, f"missing fixture {name}"
    return img


# ── Layout ────────────────────────────────────────────────────────────────────


def test_hole_positions_are_830_unique_and_valid() -> None:
    holes = hole_positions_mm()
    assert len(holes) == 830
    assert len(set(holes)) == 830
    for hole, (x, y) in holes.items():
        assert BB830.is_valid(hole)
        assert 0 <= x <= BOARD_W_MM
        assert 0 <= y <= BOARD_H_MM


def test_guide_corners_match_board_aspect() -> None:
    corners = cap.guide_corners(1280, 720)
    w = corners[1][0] - corners[0][0]
    h = corners[3][1] - corners[0][1]
    assert w / h == pytest.approx(BOARD_W_MM / BOARD_H_MM, rel=1e-3)


# ── Board detection ───────────────────────────────────────────────────────────


def test_detect_board_returns_none_for_blank_image() -> None:
    blank = np.full((600, 800, 3), 255, dtype=np.uint8)
    assert cap.detect_board(blank) is None


def test_detect_board_returns_none_for_noise() -> None:
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)
    assert cap.detect_board(noise) is None


def test_detect_board_finds_white_fixture() -> None:
    img = _load("bb830_white_topdown.jpg")
    corners = cap.detect_board(img)
    assert corners is not None
    assert corners.shape == (4, 2)
    w = np.linalg.norm(corners[1] - corners[0])
    h = np.linalg.norm(corners[3] - corners[0])
    assert cap.ASPECT_MIN <= w / h <= cap.ASPECT_MAX


def _clean_board() -> np.ndarray:
    """The white fixture's precisely-registered warped board (1651x546).

    Used as a clean base for pasting onto synthetic backgrounds: the raw
    fixture photo has a fraction-of-a-degree tilt (real photography), and
    starting from an already-corrected, perfectly axis-aligned board isolates
    the thing these tests are actually about (candidate selection, scale)
    from that unrelated tilt.
    """
    return cap.analyse_image(_load("bb830_white_topdown.jpg")).warped


def _paste_on_dark(
    board: np.ndarray,
    frame_w: int,
    frame_h: int,
    board_w: int,
    off_x_frac: float = 0.0,
    off_y_frac: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Paste `board` (resized to `board_w` wide) onto a dark (25,25,25) frame.

    Returns the frame and the board's true corners within it, for comparing
    against what detect_board comes back with.
    """
    scale = board_w / board.shape[1]
    new_w, new_h = round(board.shape[1] * scale), round(board.shape[0] * scale)
    resized = cv2.resize(board, (new_w, new_h), interpolation=cv2.INTER_AREA)
    frame = np.full((frame_h, frame_w, 3), 25, dtype=np.uint8)
    cx = (frame_w - new_w) // 2 + round(off_x_frac * frame_w)
    cy = (frame_h - new_h) // 2 + round(off_y_frac * frame_h)
    frame[cy : cy + new_h, cx : cx + new_w] = resized
    true_corners = np.float32([[cx, cy], [cx + new_w, cy], [cx + new_w, cy + new_h], [cx, cy + new_h]])
    return frame, true_corners


# A camera frame with the board filling roughly half its width -- the
# realistic case for a phone a comfortable distance from the board (see
# capture.py module docstring), not the fixture's own native ~1044px.
_CAMERA_FRAME_W, _CAMERA_FRAME_H = 1920, 1080
_REALISTIC_BOARD_W = 1010


def test_detect_board_ignores_dark_background_blob_centered() -> None:
    """A board-on-dark-background photo shouldn't make detect_board pick the whole frame.

    Regression test: THRESH_BINARY_INV at a fixed high threshold ("white on
    white" method) used to turn a dark background *and* the board into one
    frame-sized blob that could win on area -- see capture.py's
    `_border_touch_count`/`_candidate_contrasts`.
    """
    board = _clean_board()
    frame, true_corners = _paste_on_dark(board, _CAMERA_FRAME_W, _CAMERA_FRAME_H, _REALISTIC_BOARD_W)
    corners = cap.detect_board(frame)
    assert corners is not None
    assert np.abs(corners - true_corners).max() < 5

    result = cap.analyse_image(frame)
    assert result.present
    assert result.empty
    assert result.residual_px < 3.0


def test_detect_board_ignores_dark_background_blob_off_center() -> None:
    board = _clean_board()
    frame, true_corners = _paste_on_dark(
        board, _CAMERA_FRAME_W, _CAMERA_FRAME_H, _REALISTIC_BOARD_W, off_x_frac=0.15
    )
    corners = cap.detect_board(frame)
    assert corners is not None
    assert np.abs(corners - true_corners).max() < 5

    result = cap.analyse_image(frame)
    assert result.present
    assert result.empty
    assert result.residual_px < 3.0


def test_detect_board_ignores_dark_background_blob_with_prefer_near() -> None:
    """Same as the off-centre case, but via the live loop's prefer_near=guide path."""
    board = _clean_board()
    frame, true_corners = _paste_on_dark(
        board, _CAMERA_FRAME_W, _CAMERA_FRAME_H, _REALISTIC_BOARD_W, off_x_frac=0.15
    )
    guide = cap.guide_corners(_CAMERA_FRAME_W, _CAMERA_FRAME_H)
    corners = cap.detect_board(frame, prefer_near=guide)
    assert corners is not None
    assert np.abs(corners - true_corners).max() < 5


def test_realistic_camera_scale_is_present_and_empty() -> None:
    """Board at ~55% of a 1920x1080 frame's width -- see capture.py docstring for the
    intended framing distance. This is the scale that actually matters for the live
    demo, not the fixture's native ~1044px-wide photo."""
    board = _clean_board()
    frame, _ = _paste_on_dark(board, _CAMERA_FRAME_W, _CAMERA_FRAME_H, _REALISTIC_BOARD_W)
    result = cap.analyse_image(frame)
    assert result.present
    assert result.empty
    assert result.residual_px < 3.0


# ── Registration ──────────────────────────────────────────────────────────────


def test_register_holes_white_fixture_is_precise() -> None:
    result = cap.analyse_image(_load("bb830_white_topdown.jpg"))
    assert result.present
    assert result.residual_px < 3.0


def test_register_holes_clear_fixture_present() -> None:
    result = cap.analyse_image(_load("bb830_clear_topdown.jpg"))
    assert result.present
    assert len(result.hole_px) == 830


# ── Orientation ───────────────────────────────────────────────────────────────


def test_white_fixture_is_not_upside_down() -> None:
    result = cap.analyse_image(_load("bb830_white_topdown.jpg"))
    assert result.upside_down is False


def test_rotated_180_is_detected_upside_down() -> None:
    rotated = cv2.rotate(_load("bb830_white_topdown.jpg"), cv2.ROTATE_180)
    result = cap.analyse_image(rotated)
    assert result.present
    assert result.upside_down is True


# ── Portrait frames ──────────────────────────────────────────────────────────


def test_ensure_landscape_leaves_landscape_frame_alone() -> None:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    assert cap.ensure_landscape(frame) is frame


def test_ensure_landscape_rotates_portrait_frame() -> None:
    frame = np.zeros((800, 600, 3), dtype=np.uint8)
    rotated = cap.ensure_landscape(frame)
    assert rotated.shape[:2] == (600, 800)


def test_portrait_frame_is_present_and_empty() -> None:
    """A phone mounted pointing down can hand OpenCV a portrait frame (e.g.
    1080x1920) with the board's long axis running along the frame's tall
    dimension. analyse_image must rotate this to landscape before detecting,
    or the board only gets a fraction of the frame's actual resolution and
    registration falls apart (see capture.py's ensure_landscape)."""
    board = _clean_board()
    sideways = cv2.rotate(board, cv2.ROTATE_90_COUNTERCLOCKWISE)  # long axis now vertical, 546x1651
    portrait_w, portrait_h = 1080, 1920
    nh, nw = sideways.shape[:2]
    frame = np.full((portrait_h, portrait_w, 3), 25, dtype=np.uint8)
    cx, cy = (portrait_w - nw) // 2, (portrait_h - nh) // 2
    frame[cy : cy + nh, cx : cx + nw] = sideways

    result = cap.analyse_image(frame)
    assert result.present
    assert result.empty
    assert result.residual_px < 3.0


# ── Empty check ───────────────────────────────────────────────────────────────


def test_white_fixture_is_empty() -> None:
    result = cap.analyse_image(_load("bb830_white_topdown.jpg"))
    assert result.empty is True
    assert result.occupied == []


def test_fake_wire_is_detected_as_occupied() -> None:
    img = _load("bb830_white_topdown.jpg")
    holes = hole_positions_mm()
    # Draw a thick, saturated "jumper wire" between two holes on the same row,
    # at their real pixel positions in this specific photo (measured the same
    # way bb830_layout's constants were: see that module's docstring).
    x0, y0, sx, sy = 68, 80, 6.274, 6.167

    def to_px(hole: str) -> tuple[int, int]:
        x, y = holes[hole]
        return round(x0 + x * sx), round(y0 + y * sy)

    cv2.line(img, to_px("C10"), to_px("C20"), (0, 200, 255), 10)

    result = cap.analyse_image(img)
    assert result.present
    assert result.empty is False
    covered = {f"C{r}" for r in range(10, 21)}
    assert covered <= set(result.occupied)


def test_clear_fixture_flags_unreliable_or_reports_state() -> None:
    result = cap.analyse_image(_load("bb830_clear_topdown.jpg"))
    assert result.present
    # A translucent board isn't guaranteed reliable emptiness detection; either
    # it works, or the result says so instead of silently guessing.
    if not result.empty:
        assert result.warnings, "occupied result on the clear board should explain why"


# ── Baseline save ─────────────────────────────────────────────────────────────


def test_save_baseline_writes_expected_files(tmp_path: Path) -> None:
    result = cap.analyse_image(_load("bb830_white_topdown.jpg"))
    out = cap.save_baseline(result, _load("bb830_white_topdown.jpg"), tmp_path)

    assert (out / "baseline_raw.jpg").exists()
    assert (out / "baseline_warped.png").exists()
    assert (out / "baseline_annotated.png").exists()

    data = json.loads((out / "baseline.json").read_text())
    assert data["board"] == "bb830"
    assert data["empty"] is True
    assert data["occupied"] == []
    assert len(data["holes"]) == 830
    assert data["holes"]["A1"]["px"]
    assert data["holes"]["A1"]["bgr_mean"]


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_main_still_image_exit_code_zero_when_empty(tmp_path: Path) -> None:
    code = cap.main(
        [
            "--image",
            str(FIXTURES / "bb830_white_topdown.jpg"),
            "--no-show",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0


# ── Live loop robustness ─────────────────────────────────────────────────────


def _stub_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real windows during tests: stub every cv2 GUI call _run_live makes."""
    for name in ("imshow", "namedWindow", "resizeWindow", "destroyAllWindows"):
        monkeypatch.setattr(cap.cv2, name, lambda *a, **k: None)


def test_run_live_skips_transient_camera_read_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A few bad cap.read()s (Continuity Camera hiccups) shouldn't end the session."""
    board = _clean_board()
    frame, _ = _paste_on_dark(board, _CAMERA_FRAME_W, _CAMERA_FRAME_H, _REALISTIC_BOARD_W)
    reads = {"n": 0}
    fail_count = 5

    class FakeCapture:
        def __init__(self, index: int) -> None:
            pass

        def isOpened(self) -> bool:
            return True

        def set(self, *a: object, **k: object) -> bool:
            return True

        def read(self) -> tuple[bool, np.ndarray | None]:
            reads["n"] += 1
            if reads["n"] <= fail_count:
                return False, None
            return True, frame.copy()

        def release(self) -> None:
            pass

    _stub_gui(monkeypatch)
    monkeypatch.setattr(cap.cv2, "waitKey", lambda *a, **k: 32)  # space: capture on the first good frame
    monkeypatch.setattr(cap.cv2, "VideoCapture", FakeCapture)

    code = cap._run_live(0, tmp_path)
    assert reads["n"] > fail_count  # kept reading past the transient failures
    assert code in (0, 1)  # didn't give up with "failed to read a frame" (2)


def test_run_live_gives_up_after_sustained_camera_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reads = {"n": 0}

    class AlwaysFailsCapture:
        def __init__(self, index: int) -> None:
            pass

        def isOpened(self) -> bool:
            return True

        def set(self, *a: object, **k: object) -> bool:
            return True

        def read(self) -> tuple[bool, np.ndarray | None]:
            reads["n"] += 1
            return False, None

        def release(self) -> None:
            pass

    _stub_gui(monkeypatch)
    monkeypatch.setattr(cap.cv2, "VideoCapture", AlwaysFailsCapture)

    code = cap._run_live(0, tmp_path)
    assert code == 2
    assert reads["n"] == cap.MAX_CONSECUTIVE_READ_FAILURES


def test_run_live_unopenable_camera_lists_things_to_try(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class UnopenableCapture:
        def __init__(self, index: int) -> None:
            pass

        def isOpened(self) -> bool:
            return False

        def release(self) -> None:
            pass

    monkeypatch.setattr(cap.cv2, "VideoCapture", UnopenableCapture)

    code = cap._run_live(0, tmp_path)
    assert code == 2
    out = capsys.readouterr().out
    assert "camera index" in out
    assert "Camera" in out
