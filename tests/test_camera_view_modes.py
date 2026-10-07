from config_model import CameraConfig
from hardware.camera_capabilities import ROI, FeatureCapability
from ui.camera_widgets import VIEW_MODES, CameraPanel


def test_view_mode_presets_have_expected_geometry_and_aspect_ratios():
    assert [height for height, _name, _aspect in VIEW_MODES] == [964, 720, 480, 240, 120]
    assert [aspect for _height, _name, aspect in VIEW_MODES] == ["1.34:1", "1.79:1", "2.69:1", "5.38:1", "10.77:1"]


def test_selecting_view_mode_does_not_write_camera_settings(qtbot):
    panel = CameraPanel(
        None, "Simulated", CameraConfig(identifier="view-mode-sim", name="Simulated", backend="simulation")
    )
    qtbot.addWidget(panel)

    panel.view_mode_combo.setCurrentIndex(3)

    assert panel.view_mode_combo.currentData() == 240
    assert not panel.view_mode_apply.isEnabled()
    assert "physical cameras only" in panel.view_mode_combo.toolTip()
    panel.close()


def test_camera_controls_use_compact_toolbar_and_collapsed_live_drawer(qtbot):
    panel = CameraPanel(
        None, "Simulated", CameraConfig(identifier="compact-controls", name="Simulated", backend="simulation")
    )
    qtbot.addWidget(panel)
    panel.set_controls_visibility(True)
    panel.show()

    assert panel.controls_toggle.text() == "Controls ▸"
    assert panel.controls_toggle.isChecked() is False
    assert panel.settings_drawer.isHidden()
    assert "1.34:1" not in panel.view_mode_combo.itemText(0)
    assert panel.exposure_btn.text() == "Auto"
    assert panel.gain_btn.text() == "Auto"
    assert panel.settings_drawer.isAncestorOf(panel.exposure_control)
    assert panel.settings_drawer.isAncestorOf(panel.gain_control)
    assert panel.settings_drawer.isAncestorOf(panel.gamma_control)
    assert panel.settings_drawer.isAncestorOf(panel.view_mode_status)

    panel.controls_toggle.click()

    assert panel.controls_toggle.isChecked() is True
    assert panel.controls_toggle.text() == "Controls ▾"
    assert not panel.settings_drawer.isHidden()
    panel.close()


class FakePhysicalCamera:
    identifier = "physical-test"
    is_streaming = True

    def __init__(self, roi):
        self.roi = roi
        self.apply_calls = []
        self.restore_calls = []

    def get_roi(self):
        return self.roi

    def get_frame_rate_capability(self):
        return {
            "feature": FeatureCapability("AcquisitionFrameRate", True, True, True, "float", 30.0, 1.0, 30.0),
            "enable_feature": FeatureCapability("AcquisitionFrameRateEnable", False, False, False),
        }

    def get_capabilities(self):
        return {
            "features": {
                "width_max": {"value": 1292},
                "height_max": {"value": 964},
                "offset_x": {"minimum": 0, "increment": 1},
                "offset_y": {"minimum": 0, "increment": 1},
            }
        }

    def apply_view_mode(self, width, height, maximize_rate=True):
        self.apply_calls.append((width, height, maximize_rate))
        self.roi = ROI(width, height, 0, (964 - height) // 2)
        return {"roi": self.roi, "aspect_ratio": width / height, "roi_cap_fps": 60.0, "frame_rate_fps": 60.0}

    def restore_view_mode_baseline(self, baseline):
        self.restore_calls.append(baseline)


def _physical_panel(qtbot, camera):
    panel = CameraPanel(None, "Physical", CameraConfig(identifier="physical-test", name="Physical", backend="vimba"))
    qtbot.addWidget(panel)
    panel.camera = camera
    panel._refresh_view_mode_from_camera()
    return panel


def test_full_and_custom_current_roi_selection_are_read_only(qtbot):
    full_camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    full_panel = _physical_panel(qtbot, full_camera)
    assert full_panel.view_mode_combo.currentData() == 964
    assert full_camera.apply_calls == []
    full_panel.close()

    custom_camera = FakePhysicalCamera(ROI(1280, 900, 0, 0))
    custom_panel = _physical_panel(qtbot, custom_camera)
    assert custom_panel.view_mode_combo.currentText() == "Custom/current"
    assert custom_camera.apply_calls == []
    custom_panel.close()


def test_combo_selection_is_inert_and_apply_runs_one_transaction(qtbot):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)

    panel.view_mode_combo.setCurrentIndex(3)
    assert camera.apply_calls == []
    assert panel.view_mode_apply.isEnabled()

    panel.view_mode_apply.click()
    qtbot.waitUntil(lambda: not panel._camera_mode_change_active, timeout=3000)
    assert camera.apply_calls == [(1292, 240, True)]
    assert panel.view_mode_combo.currentData() == 240
    assert camera.roi == ROI(1292, 240, 0, 362)
    panel.update_fps(67.1)
    assert "Live 67.1 FPS" in panel.view_mode_status.text()
    panel.close()
    assert len(camera.restore_calls) == 1
