"""Driver-free checks for camera display diagnostic metrics."""

import argparse
import json

import pytest
from PySide6.QtGui import QImage

from config_model import CameraConfig
from hardware.camera_capabilities import ROI, apply_roi
from hardware.simulated_camera import SimulatedCamera
from tools.camera_display_characterize import (
    InstrumentedCameraPanel,
    PaintCounter,
    _restore_settings,
    _snapshot_settings,
    coalescing_fraction,
    counter_delta,
    diagnostic_exit_status,
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

    def is_readable(self):
        return True

    def is_writeable(self):
        return True

    def get(self):
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

    def rate_maximum(self):
        pixel = self.features["PixelFormat"].get().get_name()
        height = self.features["Height"].get()
        return 60.0 if pixel == "RGB8" and height < 964 else 120.0


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
