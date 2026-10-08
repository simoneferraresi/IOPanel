import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QRadioButton, QToolButton

from config_model import CameraConfig
from ui import discovery_dialog
from ui.alignment_panel import AlignmentPanel
from ui.camera_widgets import CameraPanel
from ui.control_panel import ScanSettings
from ui.discovery_dialog import CameraDiscoveryDialog
from ui.plot_widgets import PlotWidget


def test_wavelength_scan_actions_are_compact_top_right_plot_overlays(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.resize(720, 420)
    widget.show()
    qtbot.waitExposed(widget)
    qtbot.wait(20)

    buttons = (widget.clear_btn, widget.freeze_btn, widget.load_btn, widget.save_btn, widget.screenshot_btn)
    assert all(isinstance(button, QToolButton) for button in buttons)
    assert all(button.parentWidget() is widget.overlay_controls for button in buttons)
    assert widget.overlay_controls.parentWidget() is widget.plot_container
    assert all(button.icon().isNull() is False for button in buttons)
    assert all(button.autoRaise() for button in buttons)
    assert all(button.width() <= 32 and button.height() <= 32 for button in buttons)
    assert [widget.overlay_controls.layout().itemAt(index).widget() for index in range(5)] == list(buttons)
    assert [button.objectName() for button in buttons] == [
        "scanPlotClearButton",
        "scanPlotFreezeButton",
        "scanPlotLoadButton",
        "scanPlotSaveButton",
        "scanPlotExportImageButton",
    ]
    assert widget.load_btn.toolTip() == "Open saved scan (CSV/MAT)"
    assert widget.load_btn.accessibleName() == "Open wavelength scan data"
    assert widget.load_btn.width() == widget.save_btn.width() == 28
    load_icon = widget.load_btn.icon().pixmap(widget.load_btn.iconSize()).toImage()
    expected_icon = QIcon(":/icons/folder-open.svg").pixmap(widget.load_btn.iconSize()).toImage()
    assert not load_icon.isNull()
    assert load_icon == expected_icon
    assert widget.load_btn.focusPolicy() != Qt.FocusPolicy.NoFocus
    load_requests = QSignalSpy(widget.open_scan_requested)
    widget.load_btn.click()
    assert load_requests.count() == 1

    overlay_center = widget.overlay_controls.mapTo(widget.plot_container, widget.overlay_controls.rect().center())
    assert overlay_center.x() > widget.plot_container.width() * 0.7
    assert overlay_center.y() < 40
    assert widget.plot_widget.geometry().height() == widget.plot_container.height()

    assert widget.matlab_status_label.isHidden()
    widget._set_matlab_status("Queueing scan.fig save...")
    assert widget.matlab_status_label.isVisible()
    assert widget.matlab_status_label.text() == "Queueing scan.fig save..."
    status_center = widget.matlab_status_label.mapTo(widget.plot_container, widget.matlab_status_label.rect().center())
    assert status_center.x() < widget.plot_container.width() * 0.5
    assert status_center.y() < 40
    widget._clear_matlab_status()
    assert widget.matlab_status_label.isHidden()

    widget.close()


def test_alignment_coupling_is_exclusive_and_actions_share_visual_grammar(qtbot):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)

    assert isinstance(panel.butt_coupling_cb, QRadioButton)
    assert isinstance(panel.top_coupling_cb, QRadioButton)
    assert panel.coupling_button_group.exclusive()
    assert panel.butt_coupling_cb.isChecked()
    assert not panel.top_coupling_cb.isChecked()

    panel.set_hardware_ready(True)
    panel.top_coupling_cb.click()
    assert panel.top_coupling_cb.isChecked()
    assert not panel.butt_coupling_cb.isChecked()
    assert panel._alignment_settings().coupling_type == "top"

    for button in (panel.spiral_align_button, panel.align_button, panel.map_button):
        assert button.minimumHeight() == 35
        assert button.icon().isNull() is False
        assert "background-color: #2e7d32" in button.styleSheet()

    assert panel.stop_operation_button.minimumHeight() == 35
    assert panel.stop_operation_button.icon().isNull() is False
    assert "background-color: #c62828" in panel.stop_operation_button.styleSheet()

    panel.close()


def test_camera_capture_and_discovery_use_camera_and_refresh_actions(qtbot, monkeypatch):
    camera = CameraPanel(
        None,
        "Camera Test",
        CameraConfig(identifier="camera-test", enabled=True, name="Camera Test", backend="simulation"),
    )
    qtbot.addWidget(camera)
    assert camera.screenshot_btn.icon().isNull() is False
    assert camera.screenshot_btn.toolTip() == "Save camera screenshot"
    assert camera.screenshot_btn.accessibleName() == "Save camera screenshot"
    assert camera.video_container.layout().indexOf(camera.overlay_actions) == 1
    assert camera.settings_button.icon().isNull() is False

    monkeypatch.setattr(discovery_dialog, "VIMBA_AVAILABLE", False)
    dialog = CameraDiscoveryDialog()
    qtbot.addWidget(dialog)
    assert dialog.refresh_button.icon().isNull() is False
    assert dialog.refresh_button.toolTip() == "Refresh the detected camera list"
    assert dialog.refresh_button.accessibleName() == "Refresh camera discovery list"

    dialog.close()
    camera.close()


def test_camera_discovery_dialog_runs_camera_listing_off_gui_thread(qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def delayed_list(**_kwargs):
        entered.set()
        release.wait(1)
        finished.set()
        return []

    monkeypatch.setattr(discovery_dialog, "VIMBA_AVAILABLE", True)
    monkeypatch.setattr(discovery_dialog.VimbaCam, "list_cameras", delayed_list)
    dialog = CameraDiscoveryDialog()
    qtbot.addWidget(dialog)

    assert entered.wait(0.5)
    qtbot.wait(25)
    assert dialog.refresh_button.isEnabled() is False
    dialog.close()
    release.set()
    assert finished.wait(0.5)
