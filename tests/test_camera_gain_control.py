from __future__ import annotations

from config_model import CameraConfig
from hardware.camera import VimbaCam
from ui.camera_widgets import CameraPanel


class Feature:
    def __init__(self, value, bounds=None, increment=None, writable=True):
        self.value = value
        self.bounds = bounds
        self.increment = increment
        self.writable = writable
        self.fail_write = False

    def get(self):
        return self.value

    def set(self, value):
        if not self.writable or self.fail_write:
            raise RuntimeError("feature write failed")
        self.value = value

    def is_readable(self):
        return True

    def is_writeable(self):
        return self.writable

    def get_range(self):
        if self.bounds is None:
            raise RuntimeError("no range")
        return self.bounds

    def get_increment(self):
        return self.increment


class Device:
    def __init__(self, gain_supported=True):
        self.features = {
            "Gamma": Feature(1.0, (0.1, 4.0), 0.01),
            "ExposureTimeAbs": Feature(5000.0, (10.0, 100000.0), 1.0),
            "ExposureAuto": Feature("Off"),
            "GainAuto": Feature("Once"),
        }
        if gain_supported:
            self.features["Gain"] = Feature(5.0, (2.0, 22.0), 0.1)

    def get_feature_by_name(self, name):
        if name not in self.features:
            raise KeyError(name)
        return self.features[name]


def _panel(device, qtbot):
    camera = VimbaCam("fake-camera", camera_name="Fake")
    camera.device = device
    panel = CameraPanel(camera, "Fake", CameraConfig(identifier="fake-camera", name="Fake"))
    qtbot.addWidget(panel)
    return camera, panel


def test_manual_gain_uses_camera_range_and_disables_auto_gain(qtbot):
    device = Device()
    camera, panel = _panel(device, qtbot)
    assert panel.gain_control.isEnabled()
    assert panel.gain_control.min_val == 2.0
    assert panel.gain_control.max_val == 22.0
    assert panel.gain_control.edit.text() == "5.00"

    panel._handle_parameter_changed("gain", 8.0)

    assert camera.gain_db == 8.0
    assert device.features["GainAuto"].value == "Off"
    assert camera.set_auto_gain_once()
    assert device.features["GainAuto"].value == "Once"
    panel.close()


def test_manual_gain_write_failure_reads_back_camera_value(qtbot):
    device = Device()
    camera, panel = _panel(device, qtbot)
    device.features["Gain"].fail_write = True
    panel._handle_parameter_changed("gain", 9.0)
    qtbot.wait(150)
    assert camera.gain_db == 5.0
    assert panel.gain_control.edit.text() == "5.00"
    panel.close()


def test_manual_gain_control_is_disabled_when_feature_is_unsupported(qtbot):
    _camera, panel = _panel(Device(gain_supported=False), qtbot)
    assert not panel.gain_control.isEnabled()
    panel.close()
