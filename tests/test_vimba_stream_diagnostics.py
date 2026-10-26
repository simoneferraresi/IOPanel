"""SDK-free regression tests for the two approved VmbPy streaming diagnostics."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from tools import _vimba_stream_diagnostic as diagnostic
from tools import vimba_b2_top_stream as top
from tools import vimba_b3_side_stream as side


class FakeFrame:
    def __init__(self, index: int, *, fail_metadata: bool = False) -> None:
        self.index = index
        self.fail_metadata = fail_metadata

    def get_status(self) -> str:
        return "Complete"

    def get_width(self) -> int:
        if self.fail_metadata:
            raise RuntimeError("frame metadata unavailable")
        return 8

    def get_height(self) -> int:
        return 8

    def get_pixel_format(self) -> str:
        return "Mono8"

    def get_id(self) -> int:
        return self.index

    def get_timestamp(self) -> int:
        return self.index * 1000

    def as_numpy_ndarray(self) -> np.ndarray:
        return np.arange(64, dtype=np.uint8).reshape(8, 8)


class FakeFeature:
    def __init__(self, value: str) -> None:
        self.value = value

    def is_readable(self) -> bool:
        return True

    def get(self) -> str:
        return self.value


class FakeCamera:
    def __init__(
        self,
        camera_id: str,
        *,
        mode: str = "Continuous",
        trigger: str = "Off",
        frame_count: int = 10,
        fail_start: bool = False,
        fail_metadata: bool = False,
        fail_requeue: bool = False,
        fail_exit: bool = False,
    ) -> None:
        self.camera_id = camera_id
        self.mode = mode
        self.trigger = trigger
        self.frame_count = frame_count
        self.fail_start = fail_start
        self.fail_metadata = fail_metadata
        self.fail_requeue = fail_requeue
        self.fail_exit = fail_exit
        self.entered = False
        self.exited = False
        self.start_calls = 0
        self.stop_calls = 0
        self.requeued: list[int] = []
        self.buffer_counts: list[int] = []

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_args):
        self.exited = True
        if self.fail_exit:
            raise RuntimeError("camera close failed")
        return False

    def get_id(self) -> str:
        return self.camera_id

    def get_model(self) -> str:
        return "Fake Mako"

    def get_feature_by_name(self, name: str) -> FakeFeature:
        if name == "AcquisitionMode":
            return FakeFeature(self.mode)
        if name == "TriggerMode":
            return FakeFeature(self.trigger)
        return FakeFeature("10")

    def start_streaming(self, *, handler, buffer_count: int) -> None:
        self.start_calls += 1
        self.buffer_counts.append(buffer_count)
        if self.fail_start:
            raise RuntimeError("stream start failed")
        for index in range(self.frame_count):
            handler(self, None, FakeFrame(index, fail_metadata=self.fail_metadata))

    def stop_streaming(self) -> None:
        self.stop_calls += 1

    def queue_frame(self, frame: FakeFrame) -> None:
        self.requeued.append(frame.index)
        if self.fail_requeue:
            raise RuntimeError("frame requeue failed")


class FakeSystem:
    def __init__(self, camera: FakeCamera | None) -> None:
        self.camera = camera
        self.entered = False
        self.exited = False
        self.requested_ids: list[str] = []

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_args):
        self.exited = True
        return False

    def get_version(self) -> str:
        return "fake-vmbc"

    def get_camera_by_id(self, camera_id: str) -> FakeCamera:
        self.requested_ids.append(camera_id)
        if self.camera is None:
            raise RuntimeError("approved camera unavailable")
        return self.camera


def install_fake_sdk(monkeypatch, camera: FakeCamera | None) -> FakeSystem:
    system = FakeSystem(camera)
    sdk = ModuleType("vmbpy")
    sdk.FrameStatus = SimpleNamespace(Complete="Complete")
    sdk.VmbSystem = SimpleNamespace(get_instance=lambda: system)
    monkeypatch.setitem(sys.modules, "vmbpy", sdk)
    monkeypatch.setattr(diagnostic, "version", lambda _name: "fake-vmbpy")
    monkeypatch.setattr(diagnostic.time, "sleep", lambda _seconds: None)
    return system


def run_launcher(monkeypatch, launcher, camera_id: str) -> int:
    monkeypatch.setattr(sys, "argv", ["camera-diagnostic", "--camera-id", camera_id])
    return launcher.main()


@pytest.mark.parametrize(
    ("launcher", "camera_id", "camera_name"),
    [
        (top, "DEV_000F315B9CE1", "Top"),
        (side, "DEV_000F315BA8F9", "Side"),
    ],
)
def test_each_launcher_streams_only_its_approved_camera(monkeypatch, capsys, launcher, camera_id, camera_name):
    camera = FakeCamera(camera_id)
    system = install_fake_sdk(monkeypatch, camera)

    assert run_launcher(monkeypatch, launcher, camera_id) == 0
    assert system.requested_ids == [camera_id]
    assert system.entered and system.exited
    assert camera.entered and camera.exited
    assert camera.start_calls == 1
    assert camera.buffer_counts == [diagnostic.FRAME_BUFFER_COUNT]
    assert camera.stop_calls == 1
    assert camera.requeued == list(range(diagnostic.TARGET_COMPLETE_FRAMES))
    assert f"RESULT: PASS — {camera_name} camera streaming path only" in capsys.readouterr().out


@pytest.mark.parametrize("launcher,wrong_id", [(top, side.APPROVED_CAMERA_ID), (side, top.APPROVED_CAMERA_ID)])
def test_launcher_rejects_other_camera_before_loading_sdk(monkeypatch, launcher, wrong_id):
    monkeypatch.delitem(sys.modules, "vmbpy", raising=False)
    with pytest.raises(SystemExit) as error:
        run_launcher(monkeypatch, launcher, wrong_id)
    assert error.value.code == 2
    assert "vmbpy" not in sys.modules


@pytest.mark.parametrize("launcher", [top, side])
def test_sdk_identity_mismatch_never_opens_unexpected_camera(monkeypatch, launcher):
    camera = FakeCamera("different-sdk-camera-id")
    system = install_fake_sdk(monkeypatch, camera)
    assert run_launcher(monkeypatch, launcher, launcher.APPROVED_CAMERA_ID) == 1
    assert system.requested_ids == [launcher.APPROVED_CAMERA_ID]
    assert system.exited
    assert not camera.entered
    assert camera.start_calls == 0


@pytest.mark.parametrize("launcher", [top, side])
def test_missing_approved_camera_fails_without_fallback(monkeypatch, launcher):
    system = install_fake_sdk(monkeypatch, None)
    assert run_launcher(monkeypatch, launcher, launcher.APPROVED_CAMERA_ID) == 1
    assert system.requested_ids == [launcher.APPROVED_CAMERA_ID]
    assert system.exited


@pytest.mark.parametrize("mode,trigger", [("SingleFrame", "Off"), ("Continuous", "On")])
def test_nonpassive_settings_do_not_start_streaming(monkeypatch, mode, trigger, capsys):
    camera = FakeCamera(top.APPROVED_CAMERA_ID, mode=mode, trigger=trigger)
    system = install_fake_sdk(monkeypatch, camera)
    assert run_launcher(monkeypatch, top, top.APPROVED_CAMERA_ID) == 2
    assert system.exited and camera.exited
    assert camera.start_calls == 0
    assert camera.stop_calls == 0
    assert "RESULT: NOT RUN" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["start", "callback", "requeue", "close"])
def test_stream_failures_still_release_resources(monkeypatch, failure, capsys):
    camera = FakeCamera(
        top.APPROVED_CAMERA_ID,
        fail_start=failure == "start",
        fail_metadata=failure == "callback",
        fail_requeue=failure == "requeue",
        fail_exit=failure == "close",
    )
    system = install_fake_sdk(monkeypatch, camera)
    assert run_launcher(monkeypatch, top, top.APPROVED_CAMERA_ID) == 1
    assert system.exited and camera.exited
    assert camera.stop_calls == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_frame_collection_deadline_stops_even_without_target(monkeypatch):
    camera = FakeCamera(side.APPROVED_CAMERA_ID, frame_count=0)
    system = install_fake_sdk(monkeypatch, camera)
    monkeypatch.setattr(diagnostic, "MAX_ACQUISITION_SECONDS", 0.0)
    assert run_launcher(monkeypatch, side, side.APPROVED_CAMERA_ID) == 1
    assert camera.stop_calls == 1
    assert camera.exited and system.exited


@pytest.mark.parametrize("launcher", [top, side])
def test_direct_script_and_module_help_do_not_require_sdk(launcher):
    root = Path(__file__).resolve().parents[1]
    script = Path(launcher.__file__).resolve()
    for command in ([sys.executable, str(script), "--help"], [sys.executable, "-m", launcher.__name__, "--help"]):
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "--camera-id" in result.stdout


def test_importing_launchers_does_not_load_vmbpy_or_initialize_cameras():
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; "
        "import tools.vimba_b2_top_stream; "
        "import tools.vimba_b3_side_stream; "
        "import tools._vimba_stream_diagnostic; "
        "assert 'vmbpy' not in sys.modules; "
        "assert 'numpy' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
