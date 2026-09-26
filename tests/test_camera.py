"""Camera intake: steady-frame capture and the scan path, with a fake camera replaying real photos."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from typer.testing import CliRunner  # noqa: E402

from benchlog.cli.main import app  # noqa: E402
from benchlog.core.project import Project  # noqa: E402
from benchlog.core.repo import Repo  # noqa: E402
from benchlog.vision import camera  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
BOARD = cv2.imread(str(FIXTURES / "bb830_white_topdown.jpg"))
EXAMPLES = Path(__file__).parent.parent / "examples" / "circuits"


class FakeCapture:
    """Replays frames from `frames(n)`; `None` means a failed read."""

    def __init__(self, frames) -> None:
        self.frames = frames
        self.n = 0
        self.released = False

    def isOpened(self) -> bool:
        return True

    def set(self, *args) -> bool:
        return True

    def read(self):
        frame = self.frames(self.n)
        self.n += 1
        return (frame is not None), frame

    def release(self) -> None:
        self.released = True


class FakeClock:
    """Advances 1/30 s per call, like a 30 fps camera."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 1 / 30
        return self.t


def with_hand(n: int) -> np.ndarray:
    """The board with a dark 'hand' moving across it."""
    frame = BOARD.copy()
    h, w = frame.shape[:2]
    x = (n * 40) % (w - 200)
    cv2.rectangle(frame, (x, h // 4), (x + 200, 3 * h // 4), (40, 50, 60), -1)
    return frame


def grab(frames, **kwargs) -> np.ndarray:
    return camera.grab_steady_frame(FakeCapture(frames), clock=FakeClock(), **kwargs)


def test_steady_board_is_captured() -> None:
    frame = grab(lambda n: BOARD.copy())
    assert frame.shape == BOARD.shape


def test_waits_for_hands_to_leave() -> None:
    cap = FakeCapture(lambda n: with_hand(n) if n < 60 else BOARD.copy())
    camera.grab_steady_frame(cap, clock=FakeClock())
    assert cap.n > 60  # nothing captured while the hand was moving


def test_moving_board_times_out() -> None:
    with pytest.raises(camera.CameraError, match="hold still"):
        grab(with_hand, timeout=3)


def test_no_board_times_out() -> None:
    empty = np.full_like(BOARD, 30)
    with pytest.raises(camera.CameraError, match="no breadboard in view"):
        grab(lambda n: empty.copy(), timeout=2)


def test_transient_read_failures_are_skipped() -> None:
    frame = grab(lambda n: None if n % 3 == 0 else BOARD.copy())
    assert frame is not None


def test_dead_camera_raises_with_hints() -> None:
    with pytest.raises(camera.CameraError, match="stopped delivering frames"):
        grab(lambda n: None)


def test_portrait_frames_are_rotated() -> None:
    portrait = cv2.rotate(BOARD, cv2.ROTATE_90_COUNTERCLOCKWISE)
    frame = grab(lambda n: portrait.copy())
    assert frame.shape[1] > frame.shape[0]


def test_unopenable_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    class Unopenable:
        def __init__(self, index: int) -> None:
            pass

        def isOpened(self) -> bool:
            return False

        def release(self) -> None:
            pass

    monkeypatch.setattr(camera.cv2, "VideoCapture", Unopenable)
    with pytest.raises(camera.CameraError, match="Privacy & Security"):
        camera.open_camera(3)
    assert camera.list_cameras(max_index=2) == []


# ── The scan command with a (fake) camera ─────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "project"
    repo = Repo.init(path)
    repo.run("config", "user.name", "Test")
    repo.run("config", "user.email", "test@example.com")
    monkeypatch.chdir(path)
    Project.init(path)
    return path


def test_scan_uses_the_chosen_camera(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = []

    def fake_open(index: int) -> FakeCapture:
        opened.append(index)
        return FakeCapture(lambda n: BOARD.copy())

    monkeypatch.setattr(camera, "open_camera", fake_open)
    monkeypatch.setattr(camera, "grab_steady_frame", lambda cap, timeout, board_corners=None: cap.read()[1])
    runner = CliRunner(env={"COLUMNS": "200"})

    assert runner.invoke(app, ["camera", "use", "2"]).exit_code == 0
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 0, result.output
    assert "no changes seen" in result.output  # the empty white board matches the empty circuit
    assert runner.invoke(app, ["scan", "--camera", "1"]).exit_code == 0
    assert opened == [2, 1]


def test_scan_reports_camera_problems(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(index: int):
        raise camera.CameraError("can't open camera 0")

    monkeypatch.setattr(camera, "open_camera", broken)
    result = CliRunner().invoke(app, ["scan"])
    assert result.exit_code == 1
    assert "can't open camera 0" in result.output
