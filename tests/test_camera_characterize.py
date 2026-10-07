from __future__ import annotations

import json
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


class DynamicFrameRateFeature(FakeFeature):
    def __init__(self, camera):
        super().__init__(30.0, minimum=1.0, maximum=120.0, increment=1.0)
        self.camera = camera
        self.writes = []
        self.fail_value = None

    def get_range(self):
        return 1.0, 60.0 if self.camera.features["Height"].value > 240 else 120.0

    def set(self, value):
        self.writes.append(value)
        if value == self.fail_value:
            raise RuntimeError("simulated frame-rate restoration failure")
        self.value = value


class RoiCamera(FakeCamera):
    def __init__(self):
        super().__init__()
        self.features.update(
            {
                "Width": FakeFeature(640, minimum=1, maximum=640, increment=1),
                "Height": FakeFeature(480, minimum=1, maximum=480, increment=1),
                "OffsetX": FakeFeature(0, minimum=0, maximum=640, increment=1),
                "OffsetY": FakeFeature(0, minimum=0, maximum=480, increment=1),
                "WidthMax": FakeFeature(640),
                "HeightMax": FakeFeature(480),
                "BinningHorizontal": FakeFeature(1),
                "BinningVertical": FakeFeature(1),
                "AcquisitionFrameRateEnable": FakeFeature(False),
            }
        )
        self.features["AcquisitionFrameRate"] = DynamicFrameRateFeature(self)


@pytest.fixture
def roi_camera(monkeypatch):
    instance = RoiCamera()
    monkeypatch.setitem(sys.modules, "vmbpy", SimpleNamespace(VmbSystem=FakeVmbSystem))
    monkeypatch.setattr(characterize, "wait_for_cameras_by_id", lambda *_a, **_k: {"FAKE-CAMERA": instance})
    return instance


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


def _run_roi_test(monkeypatch, tmp_path, *, maximize=False):
    argv = [
        "camera_characterize",
        "--camera-id",
        "FAKE-CAMERA",
        "--stream-test",
        "--roi",
        "320x240",
        "--authorize-settings-changes",
        "--output",
        str(tmp_path / "roi.json"),
    ]
    if maximize:
        argv.append("--maximize-frame-rate-for-roi")
    monkeypatch.setattr(sys, "argv", argv)


def test_roi_max_frame_rate_requeries_sets_measures_and_restores(roi_camera, monkeypatch, tmp_path):
    measured_rates = []

    def capture(*_args, **_kwargs):
        measured_rates.append(roi_camera.features["AcquisitionFrameRate"].get())
        return [], [0.0, 0.01, 0.02], [1, 2, 3], 0

    monkeypatch.setattr(characterize, "_capture", capture)
    _run_roi_test(monkeypatch, tmp_path, maximize=True)

    assert characterize.main() == 0
    report = json.loads((tmp_path / "roi.json").read_text(encoding="utf-8"))
    assert report["frame_rate_capabilities"]["original_roi"]["maximum"] == 60.0
    assert report["frame_rate_capabilities"]["reduced_roi"]["maximum"] == 120.0
    assert report["frame_rate_capabilities"]["reduced_roi_maximum"] == 120.0
    assert measured_rates == [120.0]
    measurement = report["measurements"][0]
    assert measurement["reported_frame_rate"] == 120.0
    assert measurement["requested_roi"] == {"width": 320, "height": 240, "offset_x": 160, "offset_y": 120}
    assert measurement["actual_roi"] == measurement["requested_roi"]
    assert measurement["frame_id_gaps"] == 0
    assert {
        "mean_interval_ms",
        "std_interval_ms",
        "median_interval_ms",
        "p95_interval_ms",
        "p99_interval_ms",
        "max_interval_ms",
    } <= set(measurement)
    assert report["restoration"] == {
        "roi_confirmed": True,
        "frame_rate_confirmed": True,
        "frame_rate_enable_confirmed": True,
        "errors": [],
    }
    assert characterize.read_roi(roi_camera) == characterize.ROI(640, 480, 0, 0)
    assert roi_camera.features["AcquisitionFrameRate"].get() == 30.0
    assert roi_camera.features["AcquisitionFrameRateEnable"].get() is False


def test_roi_test_preserves_frame_rate_without_opt_in(roi_camera, monkeypatch, tmp_path):
    measured_rates = []

    def capture(*_args, **_kwargs):
        measured_rates.append(roi_camera.features["AcquisitionFrameRate"].get())
        return [], [0.0, 0.01], [1, 2], 0

    monkeypatch.setattr(characterize, "_capture", capture)
    _run_roi_test(monkeypatch, tmp_path)

    assert characterize.main() == 0
    report = json.loads((tmp_path / "roi.json").read_text(encoding="utf-8"))
    assert measured_rates == [30.0]
    assert report["measurements"][0]["reported_frame_rate"] == 30.0
    assert roi_camera.features["AcquisitionFrameRate"].writes == []


def test_roi_frame_rate_and_roi_restore_after_stream_failure(roi_camera, monkeypatch, tmp_path):
    def fail_capture(*_args, **_kwargs):
        raise RuntimeError("simulated stream failure")

    monkeypatch.setattr(characterize, "_capture", fail_capture)
    _run_roi_test(monkeypatch, tmp_path, maximize=True)

    with pytest.raises(RuntimeError, match="simulated stream failure"):
        characterize.main()
    assert characterize.read_roi(roi_camera) == characterize.ROI(640, 480, 0, 0)
    assert roi_camera.features["AcquisitionFrameRate"].get() == 30.0
    assert roi_camera.features["AcquisitionFrameRateEnable"].get() is False


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--maximize-frame-rate-for-roi"],
        ["--stream-test", "--maximize-frame-rate-for-roi"],
        ["--stream-test", "--roi", "320x240", "--maximize-frame-rate-for-roi"],
    ],
)
def test_roi_max_rate_rejects_invalid_flag_combinations(monkeypatch, extra_args):
    monkeypatch.setitem(sys.modules, "vmbpy", SimpleNamespace(VmbSystem=FakeVmbSystem))
    monkeypatch.setattr(sys, "argv", ["camera_characterize", "--camera-id", "FAKE-CAMERA", *extra_args])
    with pytest.raises(SystemExit) as exc_info:
        characterize.main()
    assert exc_info.value.code == 2


def test_roi_max_rate_numeric_readback_mismatch_fails(roi_camera):
    feature = roi_camera.features["AcquisitionFrameRate"]
    feature.set = lambda _value: None
    with pytest.raises(RuntimeError, match="readback"):
        characterize._set(roi_camera, "frame_rate", 120.0)


def test_roi_frame_rate_restoration_failure_returns_three(roi_camera, monkeypatch, tmp_path):
    monkeypatch.setattr(characterize, "_capture", lambda *_a, **_k: ([], [0.0, 0.01], [1, 2], 0))
    roi_camera.features["AcquisitionFrameRate"].fail_value = 30.0
    _run_roi_test(monkeypatch, tmp_path, maximize=True)

    assert characterize.main() == 3
    report = json.loads((tmp_path / "roi.json").read_text(encoding="utf-8"))
    assert report["restoration"]["roi_confirmed"] is True
    assert report["restoration"]["frame_rate_confirmed"] is False
    assert report["restoration"]["errors"]
