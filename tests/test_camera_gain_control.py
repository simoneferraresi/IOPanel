from __future__ import annotations

import time

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
        self.set_calls = 0
        self.delay_seconds = 0.0

    def get(self):
        return self.value

    def set(self, value):
        self.set_calls += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
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


def test_slider_drag_coalesces_gain_writes_and_applies_latest_value(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    gain = device.features["Gain"]
    gain.delay_seconds = 0.01
    slider = panel.gain_control.slider
    slider.setSliderDown(True)

    for position in range(150, 751, 30):
        slider.setValue(position)

    assert gain.set_calls == 0
    slider.setSliderDown(False)
    expected = panel.gain_control.value()

    assert gain.set_calls == 1
    assert gain.value == expected
    panel.close()


def test_typed_gain_change_remains_immediate(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    gain = device.features["Gain"]

    panel._handle_parameter_changed("gain", 8.0)

    assert gain.set_calls == 1
    assert gain.value == 8.0
    panel.close()


def test_pending_slider_change_is_cancelled_on_shutdown(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    gain = device.features["Gain"]
    panel.gain_control.slider.setSliderDown(True)
    panel.gain_control.slider.setValue(600)
    assert panel._pending_parameter_updates

    panel.prepare_camera_shutdown()
    qtbot.wait(75)

    assert gain.set_calls == 0
    panel.close()


def test_pending_slider_change_is_discarded_when_camera_changes(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    old_gain = device.features["Gain"]
    panel.gain_control.slider.setSliderDown(True)
    panel.gain_control.slider.setValue(600)
    replacement, replacement_panel = _panel(Device(), qtbot)
    panel.camera = replacement
    panel.gain_control.slider.setSliderDown(False)

    qtbot.wait(50)

    assert old_gain.set_calls == 0
    assert replacement_panel.camera is replacement
    panel.close()
    replacement_panel.close()


def test_dragged_gain_failure_reads_back_original_value(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    gain = device.features["Gain"]
    gain.fail_write = True
    panel.gain_control.slider.setSliderDown(True)
    panel.gain_control.slider.setValue(650)
    panel.gain_control.slider.setSliderDown(False)

    qtbot.wait(150)

    assert gain.set_calls == 1
    assert gain.value == 5.0
    assert panel.gain_control.edit.text() == "5.00"
    panel.close()


def test_two_panels_keep_dragged_gain_updates_isolated(qtbot):
    first_device, second_device = Device(), Device()
    _first_camera, first_panel = _panel(first_device, qtbot)
    _second_camera, second_panel = _panel(second_device, qtbot)
    first_slider = first_panel.gain_control.slider
    second_slider = second_panel.gain_control.slider
    first_slider.setSliderDown(True)
    second_slider.setSliderDown(True)
    first_slider.setValue(700)
    second_slider.setValue(300)

    first_slider.setSliderDown(False)

    assert first_device.features["Gain"].set_calls == 1
    assert second_device.features["Gain"].set_calls == 0
    assert second_panel._pending_parameter_updates
    second_slider.setSliderDown(False)
    assert second_device.features["Gain"].set_calls == 1
    assert first_device.features["Gain"].value != second_device.features["Gain"].value
    first_panel.close()
    second_panel.close()


def test_repeated_identical_drag_values_write_once(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    slider = panel.gain_control.slider
    slider.setSliderDown(True)
    panel._handle_parameter_changed("gain", 8.0)
    panel._handle_parameter_changed("gain", 8.0)
    panel._handle_parameter_changed("gain", 8.0)
    slider.setSliderDown(False)

    assert device.features["Gain"].set_calls == 1
    assert device.features["Gain"].value == 8.0
    panel.close()


def test_recovery_cancels_queued_drag_value(qtbot):
    device = Device()
    _camera, panel = _panel(device, qtbot)
    gain = device.features["Gain"]
    panel.gain_control.slider.setSliderDown(True)
    panel.gain_control.slider.setValue(650)

    panel.recovery_started()
    panel.gain_control.slider.setSliderDown(False)
    qtbot.wait(75)

    assert gain.set_calls == 0
    assert not panel._pending_parameter_updates
    panel.close()


def test_disconnect_cancels_queued_drag_value(qtbot):
    device = Device()
    camera, panel = _panel(device, qtbot)
    gain = device.features["Gain"]
    panel.gain_control.slider.setSliderDown(True)
    panel.gain_control.slider.setValue(650)

    camera.disconnected.emit()
    qtbot.wait(75)

    assert gain.set_calls == 0
    panel.close()
