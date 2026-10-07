from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from config_model import CameraConfig
from hardware.camera_capabilities import ROI, FeatureCapability
from hardware.simulated_camera import SimulatedCamera
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
    assert "centered sensor ROI" in panel.view_mode_combo.toolTip()
    panel.close()


def test_camera_controls_use_compact_toolbar_and_collapsed_live_drawer(qtbot):
    panel = CameraPanel(
        None, "Simulated", CameraConfig(identifier="compact-controls", name="Simulated", backend="simulation")
    )
    qtbot.addWidget(panel)
    panel.show()

    assert panel.settings_button.isVisible()
    assert not panel.settings_button.isChecked()
    assert panel.overlay_actions.isVisible()
    assert panel.overlay_actions.layout().indexOf(panel.screenshot_btn) == 0
    assert panel.overlay_actions.layout().indexOf(panel.settings_button) == 1
    assert panel.screenshot_btn.isVisible()
    assert panel.screenshot_btn.toolTip() == "Save camera screenshot"
    assert panel.screenshot_btn.accessibleName() == "Save camera screenshot"
    assert panel.settings_button.toolTip() == "Show or hide camera settings"
    assert panel.settings_button.accessibleName() == "Toggle camera settings"
    assert not hasattr(panel, "view_mode_status")
    assert not any(
        "ROI" in label.text() or "Aspect" in label.text() or "ROI cap" in label.text()
        for label in panel.findChildren(QLabel)
    )
    assert panel.settings_drawer.isHidden()
    assert not panel.controls_container.isVisible()
    assert not panel.view_mode_combo.isVisible()
    assert not any(child.text() == "Screenshot" for child in panel.findChildren(type(panel.view_mode_apply)))
    assert panel.screenshot_btn.size() == panel.settings_button.size()
    assert panel.screenshot_btn.iconSize() == panel.settings_button.iconSize()
    assert "1.34:1" not in panel.view_mode_combo.itemText(0)
    assert panel.exposure_btn.text() == "Auto"
    assert panel.gain_btn.text() == "Auto"
    assert panel.settings_drawer.isAncestorOf(panel.exposure_control)
    assert panel.settings_drawer.isAncestorOf(panel.gain_control)
    assert panel.settings_drawer.isAncestorOf(panel.gamma_control)
    assert panel.controls_container.isAncestorOf(panel.view_mode_combo)
    assert panel.controls_container.isAncestorOf(panel.view_mode_apply)

    panel.settings_button.click()

    assert panel.settings_button.isChecked() is True
    assert not panel.settings_drawer.isHidden()
    panel.close()


def test_mode_combo_uses_cached_roi_without_camera_reads(qtbot):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)
    panel.resize(900, 500)
    panel.show()
    panel.settings_button.click()
    qtbot.wait(20)
    reads = {"roi": 0, "capabilities": 0}

    def count_roi_reads():
        reads["roi"] += 1
        return camera.roi

    original_caps = camera.get_capabilities

    def count_capability_reads():
        reads["capabilities"] += 1
        return original_caps()

    camera.get_roi = count_roi_reads
    camera.get_capabilities = count_capability_reads
    panel.view_mode_combo.setCurrentIndex(3)
    panel._update_view_mode_availability()
    availability_updates = 0
    original_update = panel._update_view_mode_availability

    def count_availability_updates(*args):
        nonlocal availability_updates
        availability_updates += 1
        return original_update(*args)

    panel._update_view_mode_availability = count_availability_updates
    panel.view_mode_combo.showPopup()
    popup = panel.view_mode_combo.view()
    qtbot.waitUntil(popup.isVisible, timeout=1000)
    for index in range(panel.view_mode_combo.count()):
        rect = popup.visualRect(panel.view_mode_combo.model().index(index, 0))
        QTest.mouseMove(popup.viewport(), rect.center(), 10)
        qtbot.wait(25)
        assert popup.isVisible()
    panel.view_mode_combo.hidePopup()

    assert reads == {"roi": 0, "capabilities": 0}
    assert availability_updates == 0
    assert panel.view_mode_apply.isEnabled()
    panel.close()


def test_popup_hover_stays_open_while_simulated_frames_arrive(qtbot):
    host = QWidget()
    layout = QHBoxLayout(host)
    panels = []
    cameras = []
    for camera_id in ("popup-live-top", "popup-live-side"):
        panel = CameraPanel(None, camera_id, CameraConfig(identifier=camera_id, name=camera_id, backend="simulation"))
        camera = SimulatedCamera(camera_id, width=24, height=16, frame_interval=0.02)
        camera.open()
        panel.set_camera(camera)
        panel.settings_button.click()
        layout.addWidget(panel)
        panels.append(panel)
        cameras.append(camera)
        qtbot.addWidget(panel)
    qtbot.addWidget(host)
    host.resize(1100, 600)
    host.show()
    qtbot.waitUntil(lambda: all(camera._frame_count >= 3 for camera in cameras), timeout=1500)
    before = [camera._frame_count for camera in cameras]
    initial_indices = [panel.view_mode_combo.currentIndex() for panel in panels]

    for panel_index, panel in enumerate(panels):
        panel.view_mode_combo.showPopup()
        popup = panel.view_mode_combo.view()
        qtbot.waitUntil(popup.isVisible, timeout=1000)
        for cycle in range(3):
            for index in range(panel.view_mode_combo.count()):
                rect = popup.visualRect(panel.view_mode_combo.model().index(index, 0))
                QTest.mouseMove(popup.viewport(), rect.center(), 10)
                if cycle == 1 and index == 2:
                    host.resize(1120, 620)
                qtbot.wait(35)
                assert popup.isVisible()
        panel.view_mode_combo.hidePopup()
        assert panel.view_mode_combo.currentIndex() == initial_indices[panel_index]

    assert all(camera._frame_count > previous + 5 for camera, previous in zip(cameras, before, strict=True))
    for panel, camera in zip(panels, cameras, strict=True):
        panel.prepare_camera_shutdown()
        camera.close()
        panel.close()
    host.close()


def test_mode_combo_keeps_width_stable_across_selections(qtbot):
    panel = CameraPanel(
        None, "Simulated", CameraConfig(identifier="stable-combo", name="Simulated", backend="simulation")
    )
    qtbot.addWidget(panel)
    panel.resize(1000, 500)
    panel.show()
    panel.set_controls_visibility(True)
    qtbot.wait(20)

    widths = []
    for index in range(panel.view_mode_combo.count()):
        panel.view_mode_combo.setCurrentIndex(index)
        panel.view_mode_combo.updateGeometry()
        qtbot.wait(1)
        widths.append(panel.view_mode_combo.width())

    assert max(widths) - min(widths) <= 1
    assert panel.view_mode_combo.width() == 230
    assert all(panel.view_mode_combo.itemData(i, Qt.ItemDataRole.ToolTipRole) is None for i in range(len(widths)))
    assert panel.view_mode_combo.toolTip() == (
        "Select a centered sensor ROI. Smaller heights increase the available camera frame-rate range."
    )
    assert all(
        panel.view_mode_combo.itemData(i, Qt.ItemDataRole.TextAlignmentRole) == int(Qt.AlignmentFlag.AlignCenter)
        for i in range(panel.view_mode_combo.count())
    )
    assert panel.view_mode_apply.width() == 68
    assert panel.view_mode_label.sizePolicy().horizontalPolicy() == panel.view_mode_label.sizePolicy().Policy.Fixed
    label_left = panel.view_mode_label.mapTo(panel.controls_container, QPoint(0, 0)).x()
    apply_right = (
        panel.view_mode_apply.mapTo(panel.controls_container, QPoint(0, 0)).x() + panel.view_mode_apply.width()
    )
    row_center = (label_left + apply_right) / 2
    assert abs(row_center - panel.controls_container.rect().center().x()) <= 1
    panel.close()


def test_exposure_gain_gamma_slider_tracks_have_equal_width(qtbot):
    panel = CameraPanel(
        None, "Simulated", CameraConfig(identifier="equal-sliders", name="Simulated", backend="simulation")
    )
    qtbot.addWidget(panel)
    panel.set_controls_visibility(True)
    panel.resize(900, 500)
    panel.show()
    qtbot.wait(30)

    widths = [
        panel.exposure_control.slider.width(),
        panel.gain_control.slider.width(),
        panel.gamma_control.slider.width(),
    ]
    assert max(widths) - min(widths) <= 1
    panel.close()


class FakePhysicalCamera:
    identifier = "physical-test"
    camera_name = "Physical"
    is_streaming = True
    is_open = True

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
        assert self.is_open
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
    panel.view_mode_combo.activated.emit(3)
    assert camera.apply_calls == []
    assert panel.view_mode_apply.isEnabled()

    panel.view_mode_apply.click()
    qtbot.waitUntil(lambda: not panel._camera_mode_change_active, timeout=3000)
    assert camera.apply_calls == [(1292, 240, True)]
    assert panel.view_mode_combo.currentData() == 240
    assert camera.roi == ROI(1292, 240, 0, 362)
    panel.update_fps(67.1)
    assert panel._current_fps == 67.1
    assert not hasattr(panel, "_render_view_mode_status")
    panel.set_controls_visibility(True)
    assert panel._current_roi == camera.roi
    panel.close()
    assert len(camera.restore_calls) == 1


def test_prepare_camera_shutdown_restores_baseline_once_before_close(qtbot):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)
    panel.view_mode_combo.setCurrentIndex(2)
    panel.view_mode_combo.activated.emit(2)
    panel.view_mode_apply.setEnabled(True)
    panel._view_mode_baseline = {"roi": camera.roi, "rate": 30.0}
    panel._view_mode_changed = True

    panel.prepare_camera_shutdown()
    panel.prepare_camera_shutdown()

    assert len(camera.restore_calls) == 1
    assert panel._panel_closing
    panel.close()


def test_camera_mode_failure_uses_temporary_feedback_without_status_row(qtbot, monkeypatch):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)
    feedback = []
    monkeypatch.setattr("ui.camera_widgets.QToolTip.showText", lambda *_args: feedback.append(_args[1]))

    panel._finish_view_mode_change(None, "injected transaction failure")

    assert feedback == ["Mode change failed: injected transaction failure"]
    assert not hasattr(panel, "view_mode_status")
    panel.close()
