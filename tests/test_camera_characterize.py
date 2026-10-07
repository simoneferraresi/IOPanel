from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from tools import camera_characterize as characterize


class NativeEnum:
    def __init__(self, name: str):
        self.name = name


class FakeFeature:
    def __init__(self, value, *, minimum=0, maximum=100, increment=1):
        self.value = value
        self.minimum = minimum
        self.maximum = maximum
        self.increment = increment

    def get(self):
        return NativeEnum(self.value) if isinstance(self.value, str) and self.value in {"Off", "Once"} else self.value

    def set(self, value):
        self.value = value.name if isinstance(value, NativeEnum) else value

    def is_readable(self):
        return True

    def is_writeable(self):
        return True

    def get_range(self):
        return self.minimum, self.maximum

    def get_increment(self):
        return self.increment

    def get_unit(self):
        return "us"

    def get_available_entries(self):
        return ("Off", "Once", "Continuous")


class FakeCamera:
    def __init__(self):
        self.features = {
            "AcquisitionMode": FakeFeature("Continuous"),
            "TriggerMode": FakeFeature("Off"),
            "Gain": FakeFeature(4.0, minimum=0, maximum=10, increment=1),
            "ExposureTime": FakeFeature(2000.0, minimum=100, maximum=10000, increment=100),
            "GainAuto": FakeFeature("Continuous"),
            "ExposureAuto": FakeFeature("Once"),
            "AcquisitionFrameRate": FakeFeature(30.0),
        }

    def get_feature_by_name(self, name):
        return self.features[name]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get_id(self):
        return "FAKE-CAMERA"

    def get_model(self):
        return "Fake"


class FakeVmbSystem:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    @classmethod
    def get_instance(cls):
        return cls()


@pytest.fixture
def camera(monkeypatch):
    instance = FakeCamera()
    monkeypatch.setitem(sys.modules, "vmbpy", SimpleNamespace(VmbSystem=FakeVmbSystem))
    monkeypatch.setattr(characterize, "wait_for_cameras_by_id", lambda *_a, **_k: {"FAKE-CAMERA": instance})
    return instance


def test_set_compares_native_enum_by_semantic_name(camera):
    characterize._set(camera, "gain_auto", "Off")
    assert camera.features["GainAuto"].value == "Off"


def test_set_reports_semantic_enum_mismatch(camera):
    feature = camera.features["GainAuto"]
    feature.set = lambda _value: None
    with pytest.raises(RuntimeError, match="'Continuous'.*'Off'"):
        characterize._set(camera, "gain_auto", "Off")


@pytest.mark.parametrize("logical", ["gain", "exposure"])
def test_set_keeps_strict_numeric_readback(camera, logical):
    requested = 5 if logical == "gain" else 2100
    characterize._set(camera, logical, requested)
    feature = camera.features["Gain" if logical == "gain" else "ExposureTime"]
    feature.set = lambda _value: None
    with pytest.raises(RuntimeError, match="readback"):
        characterize._set(camera, logical, requested + 1)


def test_restorable_enum_snapshot_is_a_string():
    assert characterize._restorable_value(NativeEnum("Once")) == "Once"


def _stub_capture(*_args, **_kwargs):
    return [np.zeros((2, 2), dtype=np.uint8)], [0.0, 0.1], [1, 2], 0


def test_quality_sweep_restores_native_enum_snapshots(camera, monkeypatch):
    monkeypatch.setattr(characterize, "_capture", _stub_capture)
    monkeypatch.setattr(characterize, "analyze_frames", lambda *_a, **_k: {})
    monkeypatch.setattr(characterize, "frame_timing_metrics", lambda *_a: SimpleNamespace(__dict__={}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "camera_characterize",
            "--camera-id",
            "FAKE-CAMERA",
            "--quality-sweep",
            "--authorize-settings-changes",
            "--gain-values",
            "5",
            "--exposure-values-us",
            "2100",
            "--frames",
            "1",
        ],
    )

    assert characterize.main() == 0
    assert camera.features["GainAuto"].value == "Continuous"
    assert camera.features["ExposureAuto"].value == "Once"
    assert camera.features["Gain"].value == 4.0
    assert camera.features["ExposureTime"].value == 2000.0


def test_auto_once_restores_native_enum_snapshots(camera, monkeypatch):
    monkeypatch.setattr(characterize, "_capture", _stub_capture)
    monkeypatch.setattr(characterize, "analyze_frames", lambda *_a, **_k: {})
    monkeypatch.setattr(characterize, "frame_timing_metrics", lambda *_a: SimpleNamespace(__dict__={}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "camera_characterize",
            "--camera-id",
            "FAKE-CAMERA",
            "--auto-once",
            "exposure",
            "--authorize-settings-changes",
            "--frames",
            "1",
        ],
    )

    assert characterize.main() == 0
    assert camera.features["GainAuto"].value == "Continuous"
    assert camera.features["ExposureAuto"].value == "Once"
    assert camera.features["Gain"].value == 4.0
    assert camera.features["ExposureTime"].value == 2000.0


def test_lab_runner_delegates_settings_run_without_opening_hardware(monkeypatch, tmp_path):
    from tools import camera_lab_validate as lab_validate

    calls = []
    monkeypatch.setattr(
        lab_validate.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "camera_lab_validate",
            "--camera",
            "FAKE-CAMERA=top",
            "--quality-sweep",
            "--authorize-settings-changes",
            "--gain-values",
            "5",
            "--exposure-values-us",
            "2100",
            "--frames",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert lab_validate.main() == 0
    characterize_calls = [command for command in calls if any("camera_characterize.py" in part for part in command)]
    assert len(characterize_calls) == 1
    command = characterize_calls[0]
    assert command[command.index("--camera-id") + 1] == "FAKE-CAMERA"
    assert "--quality-sweep" in command
    assert (tmp_path / "characterization.json").as_posix() in [part.replace("\\", "/") for part in command]
