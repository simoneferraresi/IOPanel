import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from config_model import CameraConfig
from hardware.camera_capabilities import ROI, FeatureCapability
from hardware.simulated_camera import SimulatedCamera
from ui.camera_widgets import VIEW_MODES, CameraPanel


def test_view_mode_presets_have_expected_geometry_and_aspect_ratios():
    assert [height for height, _name, _aspect in VIEW_MODES] == [964, 720, 480, 240, 120]
    assert [aspect for _height, _name, aspect in VIEW_MODES] == [
        "1.34:1",
        "1.79:1",
        "2.69:1",
        "5.38:1",
        "10.77:1",
    ]


def test_camera_controls_keep_feed_local_overlay_and_collapsed_settings_drawer(qtbot):
    panel = CameraPanel(
        None,
        "Simulated",
        CameraConfig(identifier="compact-controls", name="Simulated", backend="simulation"),
    )
    qtbot.addWidget(panel)
    panel.show()

    assert panel.overlay_actions.layout().indexOf(panel.screenshot_btn) == 0
    assert panel.overlay_actions.layout().indexOf(panel.settings_button) == 1
    assert panel.screenshot_btn.icon().isNull() is False
    assert panel.screenshot_btn.toolTip() == "Save camera screenshot"
    assert panel.screenshot_btn.accessibleName() == "Save camera screenshot"
    assert panel.settings_button.icon().isNull() is False
    assert panel.settings_button.isCheckable()
    assert not panel.settings_button.isChecked()
    assert panel.controls_container.isHidden()
    assert panel.settings_drawer.isHidden()
    assert not hasattr(panel, "view_mode_status")
    assert not any(
        "ROI" in label.text() or "Aspect" in label.text() or "ROI cap" in label.text()
        for label in panel.findChildren(QLabel)
    )

    panel.settings_button.click()
    assert panel.controls_container.isVisible()
    assert panel.settings_drawer.isVisible()
    assert panel.controls_container.isAncestorOf(panel.view_mode_selector)
    assert panel.settings_drawer.isAncestorOf(panel.exposure_control)
    assert panel.settings_drawer.isAncestorOf(panel.gain_control)
    assert panel.settings_drawer.isAncestorOf(panel.gamma_control)
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
        self.calls = {name: 0 for name in ("get_roi", "get_capabilities", "get_frame_rate_capability")}

    def get_roi(self):
        self.calls["get_roi"] += 1
        return self.roi

    def get_frame_rate_capability(self):
        self.calls["get_frame_rate_capability"] += 1
        return {
            "feature": FeatureCapability("AcquisitionFrameRate", True, True, True, "float", 30.0, 1.0, 30.0),
            "enable_feature": FeatureCapability("AcquisitionFrameRateEnable", False, False, False),
        }

    def get_capabilities(self):
        self.calls["get_capabilities"] += 1
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


def test_segmented_selector_has_five_accessible_exclusive_presets(qtbot):
    panel = _physical_panel(qtbot, FakePhysicalCamera(ROI(1292, 964, 0, 0)))

    assert [panel.view_mode_buttons[h].text() for h, _label, _aspect in VIEW_MODES] == [
        "Full",
        "720",
        "480",
        "240",
        "120",
    ]
    assert [panel.view_mode_buttons[h].accessibleName() for h, _label, _aspect in VIEW_MODES] == [
        "Full camera mode — 1292 by 964",
        "720 crop — 1292 by 720",
        "480 crop — 1292 by 480",
        "240 crop — 1292 by 240",
        "120 crop — 1292 by 120",
    ]
    assert panel.view_mode_button_group.exclusive()
    assert panel.view_mode_button_group.checkedId() == 964
    panel.view_mode_buttons[720].click()
    assert panel.view_mode_button_group.checkedId() == 720
    assert sum(button.isChecked() for button in panel.view_mode_buttons.values()) == 1
    assert panel.view_mode_apply.isEnabled()
    panel.close()


def test_startup_refresh_maps_known_rois_and_custom_roi(qtbot):
    full_camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    full_panel = _physical_panel(qtbot, full_camera)
    assert full_panel.view_mode_button_group.checkedId() == 964
    assert full_panel.view_mode_custom_label.isHidden()

    crop_camera = FakePhysicalCamera(ROI(1292, 480, 0, 242))
    crop_panel = _physical_panel(qtbot, crop_camera)
    assert crop_panel.view_mode_button_group.checkedId() == 480

    custom_camera = FakePhysicalCamera(ROI(1280, 900, 0, 0))
    custom_panel = _physical_panel(qtbot, custom_camera)
    assert custom_panel.view_mode_button_group.checkedId() == -1
    assert custom_panel.view_mode_custom_label.text() == "Custom"
    assert not custom_panel.view_mode_custom_label.isHidden()
    assert not custom_panel.view_mode_apply.isEnabled()
    assert all(not button.isChecked() for button in custom_panel.view_mode_buttons.values())

    for panel in (full_panel, crop_panel, custom_panel):
        panel.close()


def test_pending_selection_has_zero_camera_io_and_apply_is_explicit(qtbot):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)
    before = camera.calls.copy()

    panel.view_mode_buttons[240].click()

    assert panel.view_mode_button_group.checkedId() == 240
    assert panel.view_mode_apply.isEnabled()
    assert camera.calls == before
    assert camera.apply_calls == []
    assert not panel._camera_mode_change_active
    assert not hasattr(panel, "_active_view_mode_worker")

    panel.view_mode_buttons[964].click()
    assert panel.view_mode_button_group.checkedId() == 964
    assert not panel.view_mode_apply.isEnabled()
    assert camera.calls == before
    assert camera.apply_calls == []

    panel.view_mode_buttons[240].click()
    panel.view_mode_apply.click()
    qtbot.waitUntil(lambda: not panel._camera_mode_change_active, timeout=3000)
    assert camera.apply_calls == [(1292, 240, True)]
    assert panel.view_mode_button_group.checkedId() == 240
    assert camera.roi == ROI(1292, 240, 0, 362)
    assert panel._current_roi == camera.roi
    assert not panel.view_mode_apply.isEnabled()
    panel.close()
    assert len(camera.restore_calls) == 1


def test_hardware_refresh_updates_selector_without_apply_transaction(qtbot):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)
    camera.roi = ROI(1292, 720, 0, 122)
    applies_before = list(camera.apply_calls)

    panel._refresh_view_mode_from_camera()

    assert panel.view_mode_button_group.checkedId() == 720
    assert camera.apply_calls == applies_before
    assert not panel.view_mode_apply.isEnabled()

    camera.roi = ROI(1280, 900, 0, 0)
    panel._refresh_view_mode_from_camera()
    assert panel.view_mode_button_group.checkedId() == -1
    assert not panel.view_mode_custom_label.isHidden()
    assert not panel.view_mode_apply.isEnabled()
    assert camera.apply_calls == applies_before
    panel.close()


def test_segmented_selector_stays_compact_centered_and_keyboard_accessible(qtbot):
    panel = _physical_panel(qtbot, FakePhysicalCamera(ROI(1292, 964, 0, 0)))
    panel.resize(900, 500)
    panel.show()
    panel.settings_button.click()
    qtbot.wait(30)

    assert panel.view_mode_selector.width() == 215
    assert all(panel.view_mode_buttons[h].height() == 28 for h, _label, _aspect in VIEW_MODES)
    assert panel.view_mode_apply.width() == 68
    assert panel.view_mode_buttons[964].focusPolicy() != Qt.FocusPolicy.NoFocus
    left = panel.view_mode_label.mapTo(panel.controls_container, panel.view_mode_label.rect().topLeft()).x()
    right = panel.view_mode_apply.mapTo(panel.controls_container, panel.view_mode_apply.rect().topRight()).x()
    assert abs((left + right) / 2 - panel.controls_container.rect().center().x()) <= 2

    panel.view_mode_buttons[720].setFocus()
    QTest.keyClick(panel.view_mode_buttons[720], Qt.Key.Key_Space)
    assert panel.view_mode_button_group.checkedId() == 720
    assert panel.view_mode_apply.isEnabled()

    panel.resize(380, 430)
    qtbot.wait(20)
    assert all(button.isVisible() for button in panel.view_mode_buttons.values())
    assert panel.view_mode_apply.geometry().right() < panel.controls_container.width()
    panel.close()


def test_live_dual_stream_selector_stress_is_inert(qtbot):
    host = QWidget()
    layout = QHBoxLayout(host)
    panels = []
    cameras = []
    mode_calls = []
    for camera_id in ("selector-live-top", "selector-live-side"):
        panel = CameraPanel(
            None,
            camera_id,
            CameraConfig(identifier=camera_id, name=camera_id, backend="simulation"),
        )
        camera = SimulatedCamera(camera_id, width=24, height=16, frame_interval=0.02)
        camera.open()
        panel.set_camera(camera)
        panel.config.backend = "vimba"
        camera.roi = ROI(1292, 964, 0, 0)
        calls = {name: 0 for name in ("get_roi", "get_capabilities", "get_frame_rate_capability", "apply_view_mode")}

        def get_roi(_camera=camera, _calls=calls):
            _calls["get_roi"] += 1
            return _camera.roi

        def get_capabilities(_calls=calls):
            _calls["get_capabilities"] += 1
            return {
                "features": {
                    "width_max": {"value": 1292},
                    "height_max": {"value": 964},
                    "offset_x": {"minimum": 0, "increment": 1},
                    "offset_y": {"minimum": 0, "increment": 1},
                }
            }

        def get_frame_rate_capability(_calls=calls):
            _calls["get_frame_rate_capability"] += 1
            return {"feature": FeatureCapability("fps", True, True, True, "float", 30, 1, 30)}

        camera.apply_calls = []

        def apply_view_mode(*args, _camera=camera, _calls=calls, **kwargs):
            _calls["apply_view_mode"] += 1
            _camera.apply_calls.append((args, kwargs))

        camera.get_roi = get_roi
        camera.get_capabilities = get_capabilities
        camera.get_frame_rate_capability = get_frame_rate_capability
        camera.apply_view_mode = apply_view_mode
        panel._refresh_view_mode_from_camera()
        calls.update({name: 0 for name in calls})
        panel.settings_button.click()
        layout.addWidget(panel)
        panels.append(panel)
        cameras.append(camera)
        mode_calls.append(calls)

    qtbot.addWidget(host)
    host.resize(1100, 640)
    host.show()
    qtbot.waitUntil(lambda: all(camera._frame_count >= 3 for camera in cameras), timeout=2000)
    starting_frames = [camera._frame_count for camera in cameras]
    conversion_threads = [panel.conversion_thread for panel in panels]
    settings_states = [[button.isChecked() for button in (panel.settings_button,)] for panel in panels]
    sequences = (964, 720, 480, 240, 120, 964, 240, 480)
    for height in sequences:
        QTest.mouseClick(panels[0].view_mode_buttons[height], Qt.MouseButton.LeftButton)
        assert panels[0].view_mode_button_group.checkedId() == height
        assert not panels[0]._camera_mode_change_active
        assert panels[0].view_mode_apply.isEnabled() is (height != 964)
        assert cameras[0].apply_calls == []
        assert mode_calls[0] == {
            "get_roi": 0,
            "get_capabilities": 0,
            "get_frame_rate_capability": 0,
            "apply_view_mode": 0,
        }
    qtbot.waitUntil(
        lambda: all(camera._frame_count >= start + 8 for camera, start in zip(cameras, starting_frames)), timeout=2000
    )

    assert all(
        thread is panel.conversion_thread and thread.isRunning() for thread, panel in zip(conversion_threads, panels)
    )
    assert [panel.settings_button.isChecked() for panel in panels] == [state[0] for state in settings_states]
    for panel, camera in zip(panels, cameras):
        camera.close()
        panel.close()
    host.close()


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


def test_prepare_camera_shutdown_restores_baseline_once_before_close(qtbot):
    camera = FakePhysicalCamera(ROI(1292, 964, 0, 0))
    panel = _physical_panel(qtbot, camera)
    panel.view_mode_buttons[480].click()
    panel._view_mode_baseline = {"roi": camera.roi, "rate": 30.0}
    panel._view_mode_changed = True

    panel.prepare_camera_shutdown()
    panel.prepare_camera_shutdown()

    assert len(camera.restore_calls) == 1
    assert panel._panel_closing
    panel.close()


def test_closeevent_conversion_teardown_is_idempotent(qtbot):
    camera = SimulatedCamera("idempotent-close", width=16, height=12, frame_interval=0.01)
    panel = CameraPanel(
        None, "Simulated", CameraConfig(identifier="idempotent-close", name="Simulated", backend="simulation")
    )
    qtbot.addWidget(panel)
    panel.set_camera(camera)
    camera.open()
    qtbot.waitUntil(lambda: camera._frame_count >= 2, timeout=1500)
    thread = panel.conversion_thread
    assert thread is not None
    finished = QSignalSpy(thread.finished)
    camera.close()

    panel.close()
    panel.close()

    assert not thread.isRunning()
    assert finished.count() == 1
    assert panel.conversion_worker is None
    assert panel._conversion_shutdown_complete


def test_close_after_conversion_worker_deletion_is_harmless(qtbot):
    camera = SimulatedCamera("finished-worker-close", width=16, height=12, frame_interval=0.01)
    panel = CameraPanel(
        None,
        "Simulated",
        CameraConfig(identifier="finished-worker-close", name="Simulated", backend="simulation"),
    )
    qtbot.addWidget(panel)
    panel.set_camera(camera)
    worker = panel.conversion_worker
    thread = panel.conversion_thread
    assert thread is not None and worker is not None
    finished = QSignalSpy(thread.finished)
    camera.open()
    qtbot.waitUntil(lambda: camera._frame_count >= 2, timeout=1500)
    camera.close()

    thread.quit()
    assert thread.wait(3000)
    qtbot.wait(20)
    assert not shiboken6.isValid(worker)
    assert finished.count() == 1
    panel.close()
    panel._teardown_conversion_pipeline()
    panel.close()

    assert panel.conversion_worker is None
    assert panel._conversion_shutdown_complete
    assert not thread.isRunning()
    assert finished.count() == 1
    assert worker is not None
