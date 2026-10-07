"""Driver-free checks for camera display diagnostic metrics."""

import argparse
import json
import sys
import types
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QWidget

import tools.camera_display_characterize as diagnostic
from config_model import CameraConfig
from hardware.camera_capabilities import ROI, apply_roi
from hardware.simulated_camera import SimulatedCamera
from tools.camera_display_characterize import (
    DiagnosticCameraWindow,
    InstrumentedCameraPanel,
    PaintCounter,
    _restore_settings,
    _snapshot_settings,
    coalescing_fraction,
    counter_delta,
    diagnostic_exit_status,
    paint_metrics,
    parse_size,
    timing_stats,
    validate_args,
)


def test_counter_deltas_do_not_report_lifetime_totals():
    assert counter_delta(120, 147) == 27
    assert counter_delta(147, 120) == 0


def test_coalescing_fraction_handles_empty_denominator():
    assert coalescing_fraction(3, 12) == 0.25
    assert coalescing_fraction(3, 0) == 0.0


def test_timing_statistics_use_interval_samples():
    result = timing_stats([1.0, 1.01, 1.03, 1.06])
    assert result["count"] == 4
    assert result["mean_seconds"] == pytest.approx(0.02)
    assert result["median_seconds"] == pytest.approx(0.02)
    assert result["p95_seconds"] == pytest.approx(0.029)
    assert result["max_seconds"] == pytest.approx(0.03)
    assert timing_stats([1.0])["max_seconds"] is None


@pytest.mark.parametrize(
    ("timestamps", "wall", "expected_count", "expected_span", "expected_coverage", "expected_active_fps"),
    [
        ([], 30.0, 0, None, 0.0, 0.0),
        ([4.0], 30.0, 1, None, 0.0, 0.0),
        ([0.1, 10.0, 20.0, 29.9], 30.0, 4, 29.8, 29.8 / 30.0, 3 / 29.8),
        ([10.0, 10.01, 10.02, 12.95], 30.0, 4, 2.95, 2.95 / 30.0, 3 / 2.95),
    ],
    ids=["zero-events", "one-event", "full-coverage", "clustered-events"],
)
def test_paint_metrics_distinguish_wall_rate_active_rate_and_coverage(
    timestamps, wall, expected_count, expected_span, expected_coverage, expected_active_fps
):
    result = paint_metrics(timestamps, wall)

    assert result["count"] == expected_count
    assert result["paint_events_per_wall_second"] == pytest.approx(expected_count / wall)
    assert result["active_span_paint_event_fps"] == pytest.approx(expected_active_fps)
    if expected_span is None:
        assert result["paint_timestamp_span_seconds"] is None
    else:
        assert result["paint_timestamp_span_seconds"] == pytest.approx(expected_span)
    assert result["paint_timestamp_coverage_fraction"] == pytest.approx(expected_coverage)
    assert result["timing"]["count"] == expected_count
    observation = "paint_timestamps_do_not_span_measurement"
    assert (observation in result["observations"]) == (expected_coverage < 0.9)


def test_presentation_call_counter_and_paint_event_counter(qtbot):
    camera = SimulatedCamera("diagnostic", width=8, height=6)
    panel = InstrumentedCameraPanel(None, "Diagnostic", CameraConfig(identifier="diagnostic", name="Diagnostic"))
    qtbot.addWidget(panel)
    panel.set_camera(camera)
    counter = PaintCounter()
    panel.video_label.installEventFilter(counter)
    panel.show()
    image = QImage(8, 6, QImage.Format.Format_Grayscale8)
    image.fill(42)
    panel._display_converted_image(image)
    qtbot.wait(20)
    assert len(panel.presentation_timestamps) == 1
    assert counter.timestamps
    panel.close()


def test_heartbeat_statistics_and_json_serializability():
    heartbeat = [0.0, 0.016, 0.032, 0.048]
    stats = timing_stats(heartbeat)
    assert stats["median_seconds"] == pytest.approx(0.016)
    result = {
        "heartbeat": stats,
        "conversion": {"max_pending_frames": 1, "max_pending_frames_at_most_one": True},
        "presentation": {"max_pending_images": 1, "max_pending_images_at_most_one": True},
    }
    assert result["conversion"]["max_pending_frames_at_most_one"]
    assert result["presentation"]["max_pending_images_at_most_one"]
    json.dumps(result)


@pytest.mark.parametrize(
    "argv",
    [
        ["--camera-id", "DEV_1", "--roi", "640x480"],
        ["--camera-id", "DEV_1", "--maximize-frame-rate-for-roi", "--authorize-settings-changes"],
        ["--camera-id", "DEV_1", "--authorize-settings-changes"],
    ],
)
def test_argument_validation_rejects_unsafe_combinations(argv):
    import argparse

    parser = argparse.ArgumentParser()
    args = argparse.Namespace(
        camera_id="DEV_1",
        roi=None,
        maximize_frame_rate_for_roi=False,
        authorize_settings_changes=False,
        duration=30,
        window_size=(960, 720),
        output=None,
    )
    if "--roi" in argv:
        args.roi = (640, 480)
    args.maximize_frame_rate_for_roi = "--maximize-frame-rate-for-roi" in argv
    args.authorize_settings_changes = "--authorize-settings-changes" in argv
    with pytest.raises(SystemExit):
        validate_args(parser, args)


def test_size_parser_requires_positive_dimensions():
    assert parse_size("960x720") == (960, 720)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size("0x720")


class EnumEntry:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeFeature:
    def __init__(self, camera, name, value, minimum=None, maximum=None):
        self.camera = camera
        self.name = name
        self.value = value
        self.minimum = minimum
        self.maximum = maximum
        self.fail_restore = False
        self.readable = True

    def is_readable(self):
        return self.readable

    def is_writeable(self):
        return True

    def get(self):
        if not self.readable:
            raise RuntimeError("feature is unreadable")
        return self.value

    def get_range(self):
        if self.name == "AcquisitionFrameRateAbs":
            return (1.0, self.camera.rate_maximum())
        if self.minimum is not None and self.maximum is not None:
            return (self.minimum, self.maximum)
        raise RuntimeError("enumeration has no numeric range")

    def get_increment(self):
        return 1

    def set(self, value):
        self.camera.operation_log.append((self.name, value))
        if self.fail_restore:
            self.fail_restore = False
            raise RuntimeError("injected Gamma write failure")
        if self.name == "AcquisitionFrameRateAbs":
            if float(value) > self.camera.rate_maximum():
                self.camera.illegal_rate_attempts.append(
                    (
                        float(value),
                        self.camera.get_feature_by_name("PixelFormat").get().get_name(),
                        self.camera.get_feature_by_name("Height").get(),
                    )
                )
                raise ValueError("frame rate is illegal in current configuration")
            self.camera.rate_write_log.append(
                (
                    float(value),
                    self.camera.get_feature_by_name("PixelFormat").get().get_name(),
                    self.camera.get_feature_by_name("Height").get(),
                )
            )
            self.value = float(value)
        elif self.name in {"AcquisitionMode", "TriggerMode", "ExposureAuto", "GainAuto", "PixelFormat"}:
            self.value = EnumEntry(str(value))
        else:
            self.value = value


class FakeGenICamCamera:
    def __init__(self):
        self.rate_write_log = []
        self.illegal_rate_attempts = []
        self.rate_range_heights = []
        self.operation_log = []
        self.features = {}
        values = {
            "Width": (1292, 4, 1292),
            "Height": (964, 4, 964),
            "OffsetX": (0, 0, 1288),
            "OffsetY": (0, 0, 960),
            "WidthMax": (1292, None, None),
            "HeightMax": (964, None, None),
            "AcquisitionFrameRateAbs": (90.0, 1.0, 120.0),
            "AcquisitionFrameRateEnable": (False, None, None),
            "AcquisitionMode": (EnumEntry("MultiFrame"), None, None),
            "TriggerMode": (EnumEntry("On"), None, None),
            "ExposureAuto": (EnumEntry("Continuous"), None, None),
            "GainAuto": (EnumEntry("Once"), None, None),
            "Gamma": (0.5, 0.0, 4.0),
            "PixelFormat": (EnumEntry("Mono8"), None, None),
            "Gain": (7.0, 0.0, 24.0),
            "ExposureTimeAbs": (1000.0, 10.0, 100000.0),
        }
        for name, (value, minimum, maximum) in values.items():
            self.features[name] = FakeFeature(self, name, value, minimum, maximum)

    def get_feature_by_name(self, name):
        return self.features[name]

    def get_model(self):
        return "Fake camera"

    def rate_maximum(self):
        pixel = self.features["PixelFormat"].get().get_name()
        height = self.features["Height"].get()
        self.rate_range_heights.append(height)
        return 60.0 if pixel == "RGB8" and height < 964 else 120.0


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot, *_args):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)


class FakeDiagnosticCamera:
    def __init__(self, _identifier, camera_name=None):
        self.identifier = _identifier
        self.device = None
        self.is_streaming = False
        self.is_mono = True
        self.frame_monitor = SimpleNamespace(get_fps=lambda: 1.0)
        self.new_frame = FakeSignal()
        self.fps_updated = FakeSignal()

    def open(self):
        self.device = camera_states.get(self.identifier, camera_state)
        self.is_streaming = True
        return True

    def get_roi(self):
        return diagnostic.read_roi(self.device)

    def get_frame_rate_capability(self):
        return {"feature": diagnostic.inspect_feature(self.device, diagnostic.FEATURE_ALIASES["frame_rate"])}

    def close(self):
        self.device = None
        self.is_streaming = False


class FakeDiagnosticPanel(QWidget):
    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.video_label = QLabel(self)
        self.conversion_worker = SimpleNamespace(
            submitted_frames=0, converted_frames=0, coalesced_frames=0, max_pending_frames=0
        )
        self.conversion_thread = None
        self.coalesced_images = 0
        self.max_pending_images = 0
        self.accepted_image_timestamps = []
        self.presentation_timestamps = []
        self._latest_pixmap = object()

    def set_camera(self, camera):
        self.camera = camera


camera_state: FakeGenICamCamera
camera_states: dict[str, FakeGenICamCamera] = {}


def _run_fake_main(monkeypatch, tmp_path, camera, *, fail_rate_query=False, duration="0.01"):
    global camera_state, camera_states
    camera_state = camera
    camera_states = {"DEV_TEST": camera}
    system = SimpleNamespace(entered=False, exited=False)

    def system_enter():
        system.entered = True
        return system

    def system_exit(*_args):
        system.exited = True

    system.__enter__ = system_enter
    system.__exit__ = system_exit
    system.get_instance = lambda: system

    config_handle = camera
    config_handle.__enter__ = lambda: config_handle
    config_handle.__exit__ = lambda *_args: None
    monkeypatch.setitem(sys.modules, "vmbpy", types.SimpleNamespace(VmbSystem=system))
    monkeypatch.setattr(diagnostic, "wait_for_cameras_by_id", lambda *_args, **_kwargs: {"DEV_TEST": config_handle})
    monkeypatch.setattr(diagnostic, "VimbaCam", FakeDiagnosticCamera)
    monkeypatch.setattr(diagnostic, "CameraPanel", FakeDiagnosticPanel)
    monkeypatch.setattr(diagnostic, "InstrumentedCameraPanel", FakeDiagnosticPanel)

    original_inspect = diagnostic.inspect_feature
    failed = False

    def inspect_with_optional_failure(device, aliases):
        nonlocal failed
        if (
            fail_rate_query
            and not failed
            and "AcquisitionFrameRateAbs" in aliases
            and device.features["Height"].get() == 480
        ):
            failed = True
            raise RuntimeError("injected ROI-dependent frame-rate query failure")
        return original_inspect(device, aliases)

    if fail_rate_query:
        monkeypatch.setattr(diagnostic, "inspect_feature", inspect_with_optional_failure)
    output = tmp_path / "main-path.json"
    status = diagnostic.main(
        [
            "--camera-id",
            "DEV_TEST",
            "--roi",
            "1292x480",
            "--maximize-frame-rate-for-roi",
            "--authorize-settings-changes",
            "--duration",
            duration,
            "--output",
            str(output),
        ]
    )
    return status, json.loads(output.read_text(encoding="utf-8")), system


def test_snapshot_normalizes_native_enum_wrappers_and_leaves_manual_controls_out():
    camera = FakeGenICamCamera()
    snapshot = _snapshot_settings(camera)
    assert snapshot["acquisition_mode"]["value"] == "MultiFrame"
    assert snapshot["trigger_mode"]["value"] == "On"
    assert snapshot["pixel_format"]["value"] == "Mono8"
    assert snapshot["gamma"]["value"] == 0.5
    assert "gain" not in snapshot
    assert "exposure" not in snapshot


def test_full_restore_handles_startup_settings_roi_format_and_frame_rate():
    camera = FakeGenICamCamera()
    original = _snapshot_settings(camera)
    camera.features["PixelFormat"].set("RGB8")  # production preferred-format selection
    for name, value in (
        ("AcquisitionMode", "Continuous"),
        ("TriggerMode", "Off"),
        ("ExposureAuto", "Off"),
        ("GainAuto", "Off"),
    ):
        camera.features[name].set(value)
    camera.features["Gamma"].set(1.0)
    camera.features["AcquisitionFrameRateEnable"].set(True)
    requested_roi = ROI(1292, 480, 0, 242)
    apply_roi(camera, requested_roi)
    camera.features["AcquisitionFrameRateAbs"].set(60.0)

    result = _restore_settings(camera, original, changed_rate=True)

    assert not result["errors"]
    assert all(
        result[field]
        for field in (
            "roi_confirmed",
            "frame_rate_confirmed",
            "frame_rate_enable_confirmed",
            "acquisition_mode_confirmed",
            "trigger_mode_confirmed",
            "exposure_auto_confirmed",
            "gain_auto_confirmed",
            "gamma_confirmed",
            "pixel_format_confirmed",
        )
    )
    assert camera.features["Height"].get() == 964
    assert camera.features["PixelFormat"].get().get_name() == "Mono8"
    assert camera.features["AcquisitionFrameRateAbs"].get() == 90.0
    assert camera.features["AcquisitionFrameRateEnable"].get() is False
    assert camera.features["Gain"].get() == 7.0
    assert camera.features["ExposureTimeAbs"].get() == 1000.0
    assert not camera.illegal_rate_attempts
    exact_rate_write = next(row for row in camera.rate_write_log if row[0] == 90.0)
    assert exact_rate_write[1:] == ("Mono8", 964)


def test_best_effort_restore_continues_after_single_feature_write_failure():
    camera = FakeGenICamCamera()
    original = _snapshot_settings(camera)
    camera.features["Gamma"].set(1.0)
    camera.features["GainAuto"].set("Off")
    camera.features["Gamma"].fail_restore = True

    result = _restore_settings(camera, original, changed_rate=False)

    assert any("gamma restoration" in error for error in result["errors"])
    assert result["gamma_confirmed"] is False
    assert result["gain_auto_confirmed"] is True
    assert camera.features["GainAuto"].get().get_name() == "Once"
    assert (
        diagnostic_exit_status(
            {
                "measurement_error": None,
                "restoration": result,
                "cleanup": {"camera_closed": True, "conversion_thread_stopped": True, "VmbSystem_exited": True},
            }
        )
        != 0
    )


def test_unavailable_restore_features_are_explicitly_unconfirmed():
    camera = FakeGenICamCamera()
    original = _snapshot_settings(camera)
    original["gamma"] = {"available": False, "readable": False, "value": None}
    result = _restore_settings(camera, original, changed_rate=False)
    assert result["gamma_confirmed"] is None
    assert "gamma" in result["unavailable_features"]


def test_main_roi_max_rate_path_works_without_enable_feature(monkeypatch, tmp_path, qtbot):
    camera = FakeGenICamCamera()
    camera.features.pop("AcquisitionFrameRateEnable")

    status, result, system = _run_fake_main(monkeypatch, tmp_path, camera)

    assert status == 0, result
    assert result["measurement_error"] is None
    assert result["metrics"]["process"]["wall_seconds"] > 0
    assert 480 in camera.rate_range_heights
    assert any(row[0] == 120.0 and row[2] == 480 for row in camera.rate_write_log)
    assert camera.features["Height"].get() == 964
    assert result["restoration"]["roi_confirmed"] is True
    assert result["restoration"]["frame_rate_enable_confirmed"] is None
    assert system.exited


@pytest.mark.parametrize("enum_state", [False, True])
def test_main_roi_max_rate_enables_then_restores_original_enable_state(monkeypatch, tmp_path, qtbot, enum_state):
    camera = FakeGenICamCamera()
    if enum_state:
        camera.features["AcquisitionFrameRateEnable"].value = EnumEntry("Off")

    status, result, system = _run_fake_main(monkeypatch, tmp_path, camera)

    assert status == 0, result
    assert result["measurement_error"] is None
    enabled_value = "On" if enum_state else True
    restored_value = "Off" if enum_state else False
    enabled_index = camera.operation_log.index(("AcquisitionFrameRateEnable", enabled_value))
    maximum_index = camera.operation_log.index(("AcquisitionFrameRateAbs", 120.0))
    assert enabled_index < maximum_index
    final_enable = camera.features["AcquisitionFrameRateEnable"].get()
    if enum_state:
        assert final_enable == restored_value
    else:
        assert final_enable is restored_value
    assert result["restoration"]["frame_rate_enable_confirmed"] is True
    assert result["restoration"]["roi_confirmed"] is True
    assert system.exited


def test_main_roi_max_rate_rejects_available_unreadable_enable_and_restores_roi(monkeypatch, tmp_path, qtbot):
    camera = FakeGenICamCamera()
    camera.features["AcquisitionFrameRateEnable"].readable = False

    status, result, system = _run_fake_main(monkeypatch, tmp_path, camera)

    assert status != 0
    assert "not readable" in result["measurement_error"]
    assert camera.features["Height"].get() == 964
    assert result["restoration"]["roi_confirmed"] is True
    assert result["restoration"]["frame_rate_enable_confirmed"] is None
    assert system.exited


def test_main_roi_query_failure_restores_roi_and_closes_system(monkeypatch, tmp_path, qtbot):
    camera = FakeGenICamCamera()

    status, result, system = _run_fake_main(monkeypatch, tmp_path, camera, fail_rate_query=True)

    assert status != 0
    assert "injected ROI-dependent frame-rate query failure" in result["measurement_error"]
    assert camera.features["Height"].get() == 964
    assert result["restoration"]["roi_confirmed"] is True
    assert system.exited


def _run_fake_dual_main(monkeypatch, tmp_path, cameras, *, fail_second_roi=False, fault=None):
    global camera_state, camera_states
    camera_states = cameras
    camera_state = next(iter(cameras.values()))
    system = SimpleNamespace(entered=False, exited=False)
    system.__enter__ = lambda: setattr(system, "entered", True) or system
    system.__exit__ = lambda *_args: setattr(system, "exited", True)
    system.get_instance = lambda: system
    handles = {}
    for camera_id, camera in cameras.items():
        camera.__enter__ = lambda camera=camera: camera
        camera.__exit__ = lambda *_args: None
        camera.get_id = lambda camera_id=camera_id: camera_id
        handles[camera_id] = camera
    monkeypatch.setitem(sys.modules, "vmbpy", types.SimpleNamespace(VmbSystem=system))
    discoveries = []
    monkeypatch.setattr(
        diagnostic,
        "wait_for_cameras_by_id",
        lambda _system, ids, **_kw: discoveries.append(tuple(ids)) or {key: handles[key] for key in ids},
    )
    monkeypatch.setattr(diagnostic, "VimbaCam", FakeDiagnosticCamera)
    monkeypatch.setattr(diagnostic, "CameraPanel", FakeDiagnosticPanel)
    monkeypatch.setattr(diagnostic, "InstrumentedCameraPanel", FakeDiagnosticPanel)
    restore_calls = []
    real_restore = diagnostic._restore_settings
    monkeypatch.setattr(
        diagnostic,
        "_restore_settings",
        lambda camera, snapshot, changed: restore_calls.append(camera) or real_restore(camera, snapshot, changed),
    )
    if fault == "second_open":
        original_open = FakeDiagnosticCamera.open

        def fail_second_open(camera):
            if camera.identifier == "DEV_SIDE":
                raise RuntimeError("injected second VimbaCam open failure")
            return original_open(camera)

        monkeypatch.setattr(FakeDiagnosticCamera, "open", fail_second_open)
    elif fault == "second_conversion":

        class FailingConversionPanel(FakeDiagnosticPanel):
            def set_camera(self, camera):
                if camera.identifier == "DEV_SIDE":
                    raise RuntimeError("injected second conversion path failure")
                super().set_camera(camera)

        monkeypatch.setattr(diagnostic, "InstrumentedCameraPanel", FailingConversionPanel)
    elif fault == "first_restore":

        def fail_first_restore(camera, snapshot, changed):
            restored = real_restore(camera, snapshot, changed)
            if camera is cameras["DEV_TOP"]:
                restored["errors"].append("injected first-camera restoration failure")
            return restored

        monkeypatch.setattr(
            diagnostic,
            "_restore_settings",
            lambda camera, snapshot, changed: (
                restore_calls.append(camera) or fail_first_restore(camera, snapshot, changed)
            ),
        )
    elif fault == "first_thread_cleanup":

        def fail_close(panel):
            if getattr(panel, "camera", None) is not None and panel.camera.identifier == "DEV_TOP":
                raise RuntimeError("injected conversion thread cleanup failure")
            return QWidget.close(panel)

        monkeypatch.setattr(FakeDiagnosticPanel, "close", fail_close)
    if fail_second_roi:
        real_apply = diagnostic.apply_roi

        def fail_side(camera, roi):
            if camera is cameras["DEV_SIDE"]:
                raise RuntimeError("injected second-camera configuration failure")
            real_apply(camera, roi)

        monkeypatch.setattr(diagnostic, "apply_roi", fail_side)
    output = tmp_path / "dual-main.json"
    status = diagnostic.main(
        [
            "--camera-id",
            "DEV_TOP",
            "DEV_SIDE",
            "--camera-roi",
            "DEV_TOP=1292x480",
            "--camera-roi",
            "DEV_SIDE=1292x240",
            "--maximize-frame-rate-for-roi",
            "--authorize-settings-changes",
            "--duration",
            "0.01",
            "--output",
            str(output),
        ]
    )
    return status, json.loads(output.read_text(encoding="utf-8")), system, discoveries, restore_calls


def test_dual_main_uses_shared_discovery_independent_configuration_and_common_metrics(monkeypatch, tmp_path, qtbot):
    top, side = FakeGenICamCamera(), FakeGenICamCamera()

    def side_rate_maximum():
        height = side.features["Height"].get()
        side.rate_range_heights.append(height)
        return 80.0 if height < 964 else 120.0

    side.rate_maximum = side_rate_maximum
    status, result, system, discoveries, restore_calls = _run_fake_dual_main(
        monkeypatch, tmp_path, {"DEV_TOP": top, "DEV_SIDE": side}
    )

    assert status == 0, result
    assert discoveries == [("DEV_TOP", "DEV_SIDE"), ("DEV_TOP",), ("DEV_SIDE",)]
    assert len(restore_calls) == 2
    assert system.exited
    assert result["measurement_error"] is None
    assert result["cameras"]["DEV_TOP"]["configuration_readback"]["roi"]["height"] == 480
    assert result["cameras"]["DEV_SIDE"]["configuration_readback"]["roi"]["height"] == 240
    assert ("AcquisitionFrameRateAbs", 120.0) in top.operation_log
    assert ("AcquisitionFrameRateAbs", 80.0) in side.operation_log
    assert 480 in top.rate_range_heights and 240 in side.rate_range_heights
    assert result["aggregate"]["combined_conversion_fps"] == pytest.approx(
        sum(result["cameras"][key]["metrics"]["conversion"]["converted"] for key in ("DEV_TOP", "DEV_SIDE"))
        / result["aggregate"]["wall_seconds"]
    )
    assert all(result["cameras"][key]["cleanup"]["camera_closed"] for key in ("DEV_TOP", "DEV_SIDE"))


def test_dual_configuration_failure_restores_both_cameras_and_exits_system(monkeypatch, tmp_path, qtbot):
    top, side = FakeGenICamCamera(), FakeGenICamCamera()
    status, result, system, _discoveries, restore_calls = _run_fake_dual_main(
        monkeypatch, tmp_path, {"DEV_TOP": top, "DEV_SIDE": side}, fail_second_roi=True
    )

    assert status != 0
    assert "injected second-camera configuration failure" in result["measurement_error"]
    assert len(restore_calls) == 2
    assert all(result["cameras"][key]["restoration"]["roi_confirmed"] for key in ("DEV_TOP", "DEV_SIDE"))
    assert system.exited


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("second_open", "injected second VimbaCam open failure"),
        ("second_conversion", "injected second conversion path failure"),
        ("first_restore", "injected first-camera restoration failure"),
        ("first_thread_cleanup", "injected conversion thread cleanup failure"),
    ],
)
def test_dual_partial_failures_keep_attempting_peer_restore_and_cleanup(monkeypatch, tmp_path, qtbot, fault, message):
    top, side = FakeGenICamCamera(), FakeGenICamCamera()
    status, result, system, _discoveries, restore_calls = _run_fake_dual_main(
        monkeypatch, tmp_path, {"DEV_TOP": top, "DEV_SIDE": side}, fault=fault
    )

    assert status != 0
    assert system.exited
    assert len(restore_calls) == 2
    if fault in {"second_open", "second_conversion"}:
        assert message in result["measurement_error"]
    if fault == "first_restore":
        assert message in result["cameras"]["DEV_TOP"]["restoration"]["errors"]
        assert result["cameras"]["DEV_SIDE"]["restoration"]["roi_confirmed"] is True
    if fault == "first_thread_cleanup":
        assert any(message in error for error in result["cleanup_errors"])
        assert result["cameras"]["DEV_SIDE"]["cleanup"]["camera_closed"] is True


def test_dual_simulated_panels_render_independently_and_stop_on_window_close(qtbot):
    cameras = [
        SimulatedCamera("sim-60", width=16, height=12, frame_interval=1 / 60),
        SimulatedCamera("sim-100", width=20, height=10, frame_interval=1 / 100),
    ]
    panels = [
        InstrumentedCameraPanel(
            None, camera.identifier, CameraConfig(identifier=camera.identifier, name=camera.identifier)
        )
        for camera in cameras
    ]
    for panel, camera in zip(panels, cameras, strict=True):
        panel.set_camera(camera)
    outer = DiagnosticCameraWindow(panels)
    qtbot.addWidget(outer)
    outer.resize(1000, 420)
    outer.show()
    for camera in cameras:
        camera.open()

    qtbot.waitUntil(
        lambda: all(panel._latest_pixmap is not None and panel.conversion_worker is not None for panel in panels),
        timeout=3000,
    )
    qtbot.wait(250)
    assert all(panel.isVisible() for panel in panels)
    assert [(panel._latest_pixmap.width(), panel._latest_pixmap.height()) for panel in panels] == [(16, 12), (20, 10)]
    assert all(panel.conversion_worker.converted_frames > 0 for panel in panels)
    assert all(panel.conversion_worker.max_pending_frames <= 1 for panel in panels)
    assert panels[0].conversion_worker is not panels[1].conversion_worker

    for panel in panels:
        panel.prepare_camera_shutdown()
    for camera in cameras:
        camera.close()
    outer.close()
    assert all(panel.conversion_thread is not None and not panel.conversion_thread.isRunning() for panel in panels)


def test_repeated_dual_simulated_shutdown_keeps_producers_ahead_of_consumers(qtbot):
    for cycle, intervals in enumerate(((1 / 30, 1 / 75), (1 / 45, 1 / 90), (1 / 60, 1 / 120))):
        cameras = [
            SimulatedCamera(f"stress-{cycle}-top", width=16, height=12, frame_interval=intervals[0]),
            SimulatedCamera(f"stress-{cycle}-side", width=20, height=10, frame_interval=intervals[1]),
        ]
        panels = [
            InstrumentedCameraPanel(
                None, camera.identifier, CameraConfig(identifier=camera.identifier, name=camera.identifier)
            )
            for camera in cameras
        ]
        outer = DiagnosticCameraWindow(panels)
        qtbot.addWidget(outer)
        outer.show()
        for panel, camera in zip(panels, cameras, strict=True):
            panel.set_camera(camera)
            camera.open()
        qtbot.waitUntil(
            lambda cycle_panels=panels: all(panel.conversion_worker.converted_frames > 0 for panel in cycle_panels),
            timeout=2000,
        )

        for panel in panels:
            panel.prepare_camera_shutdown()
        for camera in cameras:
            camera.close()
            assert camera._frame_thread is None
        outer.close()

        assert all(panel.conversion_thread is not None and not panel.conversion_thread.isRunning() for panel in panels)


@pytest.mark.parametrize(
    "field",
    ["camera_closed", "conversion_thread_stopped", "VmbSystem_exited"],
)
def test_cleanup_failures_make_exit_status_nonzero(field):
    result = {
        "measurement_error": None,
        "restoration": {"errors": []},
        "cleanup": {"camera_closed": True, "conversion_thread_stopped": True, "VmbSystem_exited": True},
    }
    result["cleanup"][field] = False
    assert diagnostic_exit_status(result) != 0


def test_cleanup_errors_make_exit_status_nonzero_after_successful_measurement():
    result = {
        "measurement_error": None,
        "restoration": {"errors": []},
        "cleanup_errors": ["VimbaCam close failed"],
        "cleanup": {"camera_closed": True, "conversion_thread_stopped": True, "VmbSystem_exited": True},
    }
    assert diagnostic_exit_status(result) != 0


def test_accepted_images_are_counted_separately_from_presentations(qtbot):
    panel = InstrumentedCameraPanel(
        None, "Accepted count", CameraConfig(identifier="accepted-count", name="Accepted count")
    )
    qtbot.addWidget(panel)
    panel.show()
    images = [QImage(8, 6, QImage.Format.Format_Grayscale8) for _ in range(5)]
    for index, image in enumerate(images):
        image.fill(index)
        panel._accept_converted_image(image)
    qtbot.wait(30)
    assert len(panel.accepted_image_timestamps) == 5
    assert len(panel.presentation_timestamps) == 1
    assert panel.coalesced_images == 4
    assert len(panel.accepted_image_timestamps) > len(panel.presentation_timestamps)
    assert panel.max_pending_images <= 1
    panel.close()


def test_simulated_error_cleanup_stops_conversion_thread(qtbot):
    camera = SimulatedCamera("error-cleanup", width=8, height=6, frame_interval=0.005, fail_after_frames=2)
    panel = InstrumentedCameraPanel(
        None, "Error cleanup", CameraConfig(identifier="error-cleanup", name="Error cleanup")
    )
    qtbot.addWidget(panel)
    panel.set_camera(camera)
    panel.show()
    camera.open()
    qtbot.waitUntil(lambda: not camera.is_streaming, timeout=2000)
    thread = panel.conversion_thread
    camera.close()
    panel.close()
    assert thread is not None and not thread.isRunning()
