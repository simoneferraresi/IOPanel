"""Driver-free checks for camera display diagnostic metrics."""

import argparse
import json

import pytest
from PySide6.QtGui import QImage

from config_model import CameraConfig
from hardware.simulated_camera import SimulatedCamera
from tools.camera_display_characterize import (
    InstrumentedCameraPanel,
    PaintCounter,
    _restore_settings,
    coalescing_fraction,
    counter_delta,
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


def test_restoration_helper_reports_readback_failure():
    result = _restore_settings(
        object(),
        {"roi": object(), "frame_rate": None, "frame_rate_enable": None, "frame_rate_enable_available": False},
        changed_rate=False,
    )
    assert not result["roi_confirmed"]
    assert result["errors"]


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
