import importlib.util

import app
from config_model import CameraConfig
from ui.alignment_panel import ALIGNMENT_ACTION_BUTTON_STYLE
from ui.camera_widgets import CameraPanel
from ui.control_panel import MONITOR_BUTTON_STYLE, SCAN_BUTTON_STYLE


def test_application_configuration_selects_fusion_without_stylesheet(qapp, monkeypatch):
    stylesheet_calls = []
    font_install_calls = []

    def unexpected_stylesheet(*args, **kwargs):
        stylesheet_calls.append((args, kwargs))

    monkeypatch.setattr(qapp, "setStyleSheet", unexpected_stylesheet)
    monkeypatch.setattr(app, "install_application_fonts", lambda qt_app: font_install_calls.append(qt_app))

    app.configure_qt_application(qapp, "IOPanel test")

    assert qapp.style().objectName().lower() == "fusion"
    assert not stylesheet_calls
    assert font_install_calls == [qapp]


def test_physical_camera_panel_has_no_title_row(qapp):
    config = CameraConfig(identifier="physical-1", name="Top Camera", backend="vimba")

    panel = CameraPanel(None, "Top Camera", config)

    assert panel.title_label is None
    assert panel.main_layout.indexOf(panel.video_container) == 1
    assert panel.video_container.layout().indexOf(panel.video_label) == 0
    assert panel.video_container.layout().indexOf(panel.settings_button) == 1
    panel.close()


def test_simulated_camera_panel_shows_simulation_title(qapp):
    config = CameraConfig(identifier="sim-1", name="Top Camera", backend="simulation")

    panel = CameraPanel(None, "Top Camera [SIMULATED]", config)

    assert panel.title_label is not None
    assert "[SIMULATED]" in panel.title_label.text()
    panel.close()


def test_semantic_action_button_styles_are_local_and_state_specific():
    assert "QPushButton#scanButton { background-color: #2e7d32" in SCAN_BUTTON_STYLE
    assert 'QPushButton#scanButton[scanning="true"] { background-color: #c62828' in SCAN_BUTTON_STYLE
    assert "QPushButton#scanButton:disabled" in SCAN_BUTTON_STYLE

    assert "QPushButton#monitorButton { background-color: #1976d2" in MONITOR_BUTTON_STYLE
    assert 'QPushButton#monitorButton[monitoring="true"] { background-color: #c62828' in MONITOR_BUTTON_STYLE
    assert "QPushButton#monitorButton:disabled" in MONITOR_BUTTON_STYLE

    assert 'QPushButton#alignButton[running="true"] { background-color: #c62828' in ALIGNMENT_ACTION_BUTTON_STYLE
    assert 'QPushButton#mapButton[running="true"] { background-color: #c62828' in ALIGNMENT_ACTION_BUTTON_STYLE


def test_theme_module_remains_absent():
    assert importlib.util.find_spec("ui.theme") is None
