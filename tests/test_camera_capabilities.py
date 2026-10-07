from __future__ import annotations

import numpy as np
import pytest

from hardware.camera import VimbaCam
from hardware.camera_analysis import analyze_frames, frame_timing_metrics
from hardware.camera_capabilities import (
    FEATURE_ALIASES,
    ROI,
    apply_roi,
    inspect_camera,
    inspect_feature,
    read_roi,
    validate_roi,
)


class FakeFeature:
    def __init__(self, value, bounds=None, increment=None, writable=True, readable=True, unit=None, entries=()):
        self.value = value
        self.bounds = bounds
        self.increment = increment
        self.writable = writable
        self.readable = readable
        self.unit = unit
        self.entries = entries

    def get(self):
        if not self.readable:
            raise RuntimeError("read denied")
        return self.value

    def set(self, value):
        if not self.writable:
            raise RuntimeError("write denied")
        self.value = value

    def is_readable(self):
        return self.readable

    def is_writeable(self):
        return self.writable

    def get_range(self):
        if self.bounds is None:
            raise RuntimeError("no numeric range")
        return self.bounds

    def get_increment(self):
        return self.increment

    def get_unit(self):
        return self.unit

    def get_available_entries(self):
        return self.entries


class FakeDevice:
    def __init__(self, features):
        self.features = features

    def get_feature_by_name(self, name):
        if name not in self.features:
            raise KeyError(name)
        return self.features[name]

    def get_id(self):
        return "fake"


def test_feature_inspection_handles_missing_readonly_ranges_and_aliases():
    device = FakeDevice(
        {
            "GainRaw": FakeFeature(12, (0, 255), 1, writable=False),
            "ExposureTime": FakeFeature(1000.0, (10.0, 100000.0), 2.0, unit="us"),
            "ExposureAuto": FakeFeature("Off", entries=("Off", "Once")),
            "Hidden": FakeFeature(3, (0, 5), readable=False),
        }
    )
    gain = inspect_feature(device, FEATURE_ALIASES["gain"])
    assert (gain.name, gain.value, gain.minimum, gain.maximum, gain.increment) == ("GainRaw", 12, 0, 255, 1)
    assert gain.readable and not gain.writable
    assert gain.value_type == "integer"
    assert inspect_feature(device, FEATURE_ALIASES["exposure"]).name == "ExposureTime"
    assert inspect_feature(device, ("Missing",)).available is False
    auto = inspect_feature(device, ("ExposureAuto",))
    assert auto.values == ("Off", "Once")
    assert auto.value_type == "enum"
    assert inspect_feature(device, ("Hidden",)).readable is False


def test_missing_optional_genicam_features_do_not_abort_camera_configuration(monkeypatch):
    camera = VimbaCam("fake-config")
    camera.device = FakeDevice({})
    monkeypatch.setattr(camera, "_set_pixel_format", lambda: None)
    camera._configure_camera()


def test_capability_report_does_not_write_features():
    feature = FakeFeature(24, (0, 30), 0.1)
    report = inspect_camera(FakeDevice({"Gain": feature}))
    assert report["features"]["gain"]["value"] == 24
    assert feature.value == 24
    assert report["features"]["width"]["available"] is False


def test_frame_rate_set_is_explicit_and_enables_control_when_needed():
    rate = FakeFeature(30.0, (1.0, 60.0), 0.5)
    enable = FakeFeature(False)
    camera = VimbaCam("fake-rate")
    camera.device = FakeDevice({"AcquisitionFrameRate": rate, "AcquisitionFrameRateEnable": enable})
    capability = camera.get_frame_rate_capability()
    assert capability["enable_required"] is True
    assert camera.set_frame_rate(45.0)
    assert enable.value is True
    assert rate.value == 45.0
    assert camera.set_frame_rate(60.1) is False
    assert rate.value == 45.0


def test_frame_rate_capability_is_refreshed_after_geometry_changes():
    height = FakeFeature(964, (8, 964), 4)

    class DynamicRate(FakeFeature):
        def get_range(self):
            return 1.0, 30.0 if height.value == 964 else 60.0

    camera = VimbaCam("fake-dynamic-rate")
    camera.device = FakeDevice({"Height": height, "AcquisitionFrameRate": DynamicRate(30.0, (1.0, 30.0), 0.1)})
    assert camera.get_frame_rate_capability()["feature"].maximum == 30.0
    height.value = 480
    assert camera.get_frame_rate_capability()["feature"].maximum == 60.0


def test_roi_validation_respects_bounds_and_increment():
    caps = {
        name: inspect_feature(FakeDevice({name: FakeFeature(value, bounds, inc)}), (name,))
        for name, value, bounds, inc in (
            ("Width", 1280, (16, 1296), 8),
            ("Height", 480, (8, 968), 8),
            ("OffsetX", 0, (0, 1280), 8),
            ("OffsetY", 0, (0, 960), 4),
        )
    }
    caps["width"] = caps.pop("Width")
    caps["height"] = caps.pop("Height")
    caps["offset_x"] = caps.pop("OffsetX")
    caps["offset_y"] = caps.pop("OffsetY")
    caps["width_max"] = inspect_feature(FakeDevice({"WidthMax": FakeFeature(1296)}), ("WidthMax",))
    caps["height_max"] = inspect_feature(FakeDevice({"HeightMax": FakeFeature(968)}), ("HeightMax",))
    validate_roi(ROI(1280, 480, 8, 240), caps)
    with pytest.raises(ValueError, match="increment"):
        validate_roi(ROI(1279, 480, 8, 240), caps)


def test_roi_apply_and_restore_keep_camera_geometry():
    device = FakeDevice(
        {
            "Width": FakeFeature(1280, (16, 1296), 8),
            "Height": FakeFeature(960, (8, 968), 8),
            "OffsetX": FakeFeature(0, (0, 1280), 8),
            "OffsetY": FakeFeature(0, (0, 960), 4),
            "WidthMax": FakeFeature(1296),
            "HeightMax": FakeFeature(968),
        }
    )
    requested = ROI(1280, 480, 0, 240)
    original = apply_roi(device, requested)
    assert read_roi(device) == requested
    apply_roi(device, original)
    assert read_roi(device) == ROI(1280, 960, 0, 0)


def test_synthetic_image_metrics_and_temporal_snr():
    frames = [np.full((4, 4), value, dtype=np.uint8) for value in (10, 12, 14)]
    metrics = analyze_frames(frames, saturation_threshold=13, near_zero_threshold=1)
    assert metrics["mean_intensity"] == pytest.approx(12)
    assert metrics["temporal_sigma"] == pytest.approx(1.633, abs=0.01)
    assert metrics["temporal_snr"] == pytest.approx(12 / 1.633, abs=0.01)
    assert metrics["near_saturated_fraction"] == pytest.approx(1 / 3)
    assert analyze_frames([np.zeros((2, 2), dtype=np.uint8)])["temporal_snr"] is None


def test_centroid_and_frame_pacing_metrics():
    frames = []
    for center_x in (2, 3, 4):
        frame = np.zeros((7, 7), dtype=np.uint8)
        frame[3, center_x] = 100
        frames.append(frame)
    centroid = analyze_frames(frames, centroid=True)
    assert centroid["centroid_mean_x"] == pytest.approx(3)
    assert centroid["centroid_mean_y"] == pytest.approx(3)
    assert centroid["centroid_sigma_x"] == pytest.approx(np.std((2, 3, 4)))
    pacing = frame_timing_metrics((0, 0.1, 0.2, 0.4), (10, 11, 13, 14))
    assert pacing.measured_fps == pytest.approx(7.5)
    assert pacing.median_interval_ms == pytest.approx(100)
    assert pacing.max_interval_ms == pytest.approx(200)
    assert pacing.frame_id_gaps == 1
