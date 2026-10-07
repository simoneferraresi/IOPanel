from __future__ import annotations

import json
from pathlib import Path

from hardware.camera_discovery import CameraNotFoundError
from tools import camera_lab_validate
from tools.camera_lab_validate import build_summary, discover_exact, parse_camera, run_baseline


class FakeCamera:
    def __init__(self, camera_id: str):
        self.camera_id = camera_id

    def get_id(self) -> str:
        return self.camera_id


class DelayedSystem:
    def __init__(self):
        self.calls = 0
        self.top = FakeCamera("TOP")
        self.side = FakeCamera("SIDE")

    def get_all_cameras(self):
        self.calls += 1
        result = [FakeCamera("DEV_Cam1")]
        if self.calls >= 2:
            result.append(self.top)
        if self.calls >= 3:
            result.append(self.side)
        return result


def test_camera_argument_requires_and_preserves_exact_id_and_label():
    assert parse_camera("DEV_123=Top") == ("DEV_123", "Top")
    assert parse_camera("DEV_123") == ("DEV_123", "DEV_123")


def test_discovery_waits_for_requested_exact_ids_and_ignores_simulator():
    system = DelayedSystem()
    result = discover_exact(system, ["TOP", "SIDE"], timeout=1)
    assert result["status"] == "PASS"
    assert result["cameras"] == {"TOP": system.top, "SIDE": system.side}
    assert "DEV_Cam1" in result["visible_ids"]
    assert result["time_to_discovery_seconds"]["TOP"] <= result["time_to_discovery_seconds"]["SIDE"]
    assert system.calls == 3


def test_summary_is_human_readable_and_json_data_keeps_statuses():
    report = {
        "timestamp": "2026-10-07T10:00:00+02:00",
        "repository_sha": "abc",
        "stages": {"discovery": {"status": "PASS"}, "dual": {"status": "NOT RUN", "reason": "capability failed"}},
    }
    text = build_summary(report)
    assert "**discovery:** PASS" in text
    assert "capability failed" in text
    assert json.dumps(report)


class FakeFeature:
    def __init__(self, value):
        self.value = value
        self.writes = 0

    def is_readable(self):
        return True

    def is_writeable(self):
        return True

    def get(self):
        return self.value

    def get_range(self):
        return 0, 100

    def get_increment(self):
        return 1

    def get_unit(self):
        return None

    def get_available_entries(self):
        return ()

    def set(self, value):
        self.writes += 1
        self.value = value


class Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class ValidationCamera(FakeCamera):
    def __init__(self, camera_id):
        super().__init__(camera_id)
        self.features = {
            "AcquisitionMode": FakeFeature("Continuous"),
            "TriggerMode": FakeFeature("Off"),
            "PixelFormat": FakeFeature("Mono8"),
            "Width": FakeFeature(1292),
            "Height": FakeFeature(964),
            "OffsetX": FakeFeature(0),
            "OffsetY": FakeFeature(0),
            "Gain": FakeFeature(2.0),
            "ExposureTime": FakeFeature(1000.0),
            "AcquisitionFrameRate": FakeFeature(30.3),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get_feature_by_name(self, name):
        if name not in self.features:
            raise KeyError(name)
        return self.features[name]

    def get_id(self):
        return self.camera_id


class ValidationSystem:
    def __init__(self, cameras):
        self.cameras = cameras

    def get_all_cameras(self):
        return self.cameras


def test_default_baseline_writes_artifacts_and_never_sets_features(tmp_path: Path):
    top, side = ValidationCamera("TOP"), ValidationCamera("SIDE")
    timing = lambda _camera, _duration: {
        "status": "PASS",
        "complete_frames": 3,
        "incomplete_frames": 0,
        "measured_fps": 30,
        "frame_id_gaps": 0,
        "callback_errors": [],
        "reported_frame_rate": 30.3,
    }
    dual = lambda cameras, _duration: {camera.get_id(): timing(camera, 0) for camera in cameras}
    report, code = run_baseline(
        [("TOP", "Top"), ("SIDE", "Side")],
        tmp_path,
        0.01,
        environment_reporter=lambda: {"python": "test", "vmbpy": "test"},
        vmb_system_factory=lambda: Context(ValidationSystem([top, side])),
        timing_capture=timing,
        dual_capture=dual,
    )
    assert code == 0
    assert report["settings_modified"] is False
    assert report["resources"]["vmbsystem_exit"] == "PASS"
    assert {path.name for path in tmp_path.iterdir()} >= {
        "environment.json",
        "discovery.json",
        "Top-capabilities.json",
        "Side-capabilities.json",
        "Top-timing.json",
        "Side-timing.json",
        "dual-timing.json",
        "summary.json",
        "summary.md",
    }
    assert all(feature.writes == 0 for camera in (top, side) for feature in camera.features.values())


def test_environment_failure_stops_before_system_or_camera_stages(tmp_path: Path):
    invoked = False

    def no_system():
        nonlocal invoked
        invoked = True
        raise AssertionError("camera stage must not run")

    report, code = run_baseline(
        [("TOP", "Top")],
        tmp_path,
        1,
        environment_reporter=lambda: (_ for _ in ()).throw(ImportError("VmbPy unavailable")),
        vmb_system_factory=no_system,
    )
    assert code == 2
    assert report["stages"]["environment"]["status"] == "FAIL"
    assert invoked is False


def test_discovery_failure_keeps_exact_missing_ids_and_closes_system(tmp_path: Path, monkeypatch):
    class TrackingSystem(ValidationSystem):
        exited = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.exited = True
            return False

    system = TrackingSystem([])
    monkeypatch.setattr(
        camera_lab_validate,
        "discover_exact",
        lambda _system, requested: {
            "status": "FAIL",
            "requested_ids": requested,
            "time_to_discovery_seconds": {},
            "visible_ids": ["DEV_Cam1"],
            "missing_ids": requested,
            "elapsed_seconds": 10,
            "cameras": {},
        },
    )
    report, code = run_baseline(
        [("TOP", "Top"), ("SIDE", "Side")],
        tmp_path,
        1,
        environment_reporter=lambda: {"python": "test", "vmbpy": "test"},
        vmb_system_factory=lambda: system,
    )
    assert code == 1
    assert report["stages"]["discovery"]["status"] == "FAIL"
    assert report["discovery"]["missing_ids"] == ["TOP", "SIDE"]
    assert report["resources"]["vmbsystem_exit"] == "PASS"
    assert system.exited
    assert not (tmp_path / "Top-capabilities.json").exists()


def test_missing_camera_discovery_names_exact_id_and_does_not_open_other_ids(monkeypatch):
    visible = FakeCamera("DEV_Cam1")

    def fail_discovery(_system, requested, **_kwargs):
        raise CameraNotFoundError(tuple(requested), 10, ("DEV_Cam1",))

    monkeypatch.setattr(camera_lab_validate, "wait_for_cameras_by_id", fail_discovery)
    result = discover_exact(ValidationSystem([visible]), ["TOP", "SIDE"], timeout=10)
    assert result["status"] == "FAIL"
    assert result["missing_ids"] == ["TOP", "SIDE"]
    assert result["visible_ids"] == ["DEV_Cam1"]
    assert result["cameras"] == {}


def test_network_audit_uses_only_read_only_powershell_queries(monkeypatch):
    commands = []

    def fake_run(args, **_kwargs):
        command = args[-1]
        commands.append(command)
        assert not any(
            token in command.casefold()
            for token in ("set-netadapter", "set-netipinterface", "set-itemproperty", "new-itemproperty")
        )

        class Result:
            stdout = "[]"

        return Result()

    monkeypatch.setattr(camera_lab_validate.sys, "platform", "win32")
    monkeypatch.setattr(camera_lab_validate.subprocess, "run", fake_run)
    result = camera_lab_validate.network_audit()
    assert result["status"] == "PASS WITH OBSERVATIONS"
    assert set(result["commands"]) == {"adapters", "ip", "properties", "statistics"}
    assert len(commands) == 4
