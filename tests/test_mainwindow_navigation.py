"""Navigation order and menu conventions must not activate physical devices."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

from app_settings import AppSettings
from config_model import AppConfig
from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020_types import PM5020State
from ui.main_window import MainWindow


def _make_window(qtbot, monkeypatch, settings):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)
    return window


def test_fresh_window_uses_monitor_scan_alignment_order(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "new.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    assert [window.tab_widget.tabText(i) for i in range(window.tab_widget.count())] == [
        "Power Monitor",
        "Wavelength Scan",
        "Auto Alignment",
        "Thorlabs PM5020",
        "IPG EDFA",
        "SHG (Simulation)",
    ]
    assert window.tab_widget.currentIndex() == 0
    assert window.tab_widget.widget(0) is window.second_tab
    assert all(window.tab_widget.widget(index).objectName() == f"DeferredTab{index}" for index in range(1, 6))
    assert window._first_tab is None
    assert window._control_panel is None
    assert window._plot_widget is None
    assert window.matlab_engine_manager.parent() is window
    assert window._alignment_tab is None
    assert window._pm5020_panel is None
    assert window._edfa_panel is None
    assert window._shg_tab is None
    window.tab_widget.setCurrentIndex(4)
    assert window.tab_widget.widget(4) is window.edfa_panel
    assert not window.edfa_panel.task.worker_thread.isRunning()
    window.tab_widget.setCurrentIndex(0)
    assert window.tab_widget.widget(4) is window.edfa_panel
    window.close()


@pytest.mark.parametrize("legacy_index", [0, 1, 2])
def test_saved_legacy_tab_never_overrides_monitor_startup(qtbot, monkeypatch, tmp_path, legacy_index):
    backend = QSettings(str(tmp_path / "previous.ini"), QSettings.Format.IniFormat)
    backend.setValue("MainWindow/active_tab", legacy_index)
    backend.setValue("MainWindow/active_tab_id", "auto_alignment")
    settings = AppSettings(backend)
    window = _make_window(qtbot, monkeypatch, settings)
    assert window.tab_widget.currentIndex() == 0
    assert window._alignment_tab is None
    window.close()
    settings.sync()
    assert backend.value("MainWindow/active_tab") == legacy_index
    assert backend.value("MainWindow/active_tab_id") == "auto_alignment"


def test_deferred_panels_initialize_once_and_reuse_same_instance(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "lazy-tabs.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    assert window.tab_widget.currentIndex() == 0
    for index, attr in ((2, "_alignment_tab"), (3, "_pm5020_panel"), (4, "_edfa_panel"), (5, "_shg_tab")):
        assert getattr(window, attr) is None
        placeholder = window.tab_widget.widget(index)
        window.tab_widget.setCurrentIndex(index)
        actual = getattr(window, attr)
        assert actual is not None and actual is window.tab_widget.widget(index)
        assert actual is not placeholder
        window.tab_widget.setCurrentIndex(0)
        window.tab_widget.setCurrentIndex(index)
        assert getattr(window, attr) is actual
    window.close()


def test_shg_lazily_brings_up_only_its_pm5020_dependency(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "shg-dependency.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    assert window._pm5020_panel is None
    assert window._shg_tab is None
    window.tab_widget.setCurrentIndex(5)
    assert window._shg_tab is window.tab_widget.widget(5)
    assert window._pm5020_panel is window.tab_widget.widget(3)
    assert window._alignment_tab is None
    assert window._edfa_panel is None
    assert not window._pm5020_panel.task.worker_thread.isRunning()
    window.close()


def test_scan_tab_constructs_once_and_shares_matlab_manager(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "scan-on-demand.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    manager = window.matlab_engine_manager
    assert window._plot_widget is None
    assert window._control_panel is None
    window.tab_widget.setCurrentIndex(1)
    assert window._plot_widget is window.plot_widget
    assert window._control_panel is window.control_panel
    assert window.plot_widget.matlab_engine_manager is manager
    assert window.tab_widget.widget(1) is window.first_tab
    scan = window.plot_widget
    window.tab_widget.setCurrentIndex(0)
    window.tab_widget.setCurrentIndex(1)
    assert window.plot_widget is scan
    window.close()


def test_unvisited_scan_tab_does_not_construct_on_close(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "never-scan.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    assert window._plot_widget is None
    window.close()
    assert window._plot_widget is None
    assert window._control_panel is None


def test_menu_order_and_action_placement(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "menus.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    menus = {
        "File": window.file_menu,
        "View": window.view_menu,
        "Instruments": window.instrument_menu,
        "Cameras": window.cameras_menu,
        "Help": window.help_menu,
    }
    assert [action.text().replace("&", "") for action in window.menuBar().actions()] == list(menus)

    def labels(name):
        return [action.text() for action in menus[name].actions() if not action.isSeparator()]

    assert labels("File") == ["Open Scan...", "Add Scan to Plot...", "E&xit"]
    assert labels("View") == ["Application Icon…", "Log Console"]
    assert "CT400 ETA Calibration…" in labels("Instruments")
    assert "Connect PM5020" in labels("Instruments")
    assert "Connect EDFA" in labels("Instruments")
    assert "CT400 ETA Calibration…" not in labels("File")
    assert labels("Cameras") == ["Discover Cameras..."]
    assert labels("Help") == ["&About"]
    assert not menus["Cameras"].actions()[0].isSeparator()
    assert not menus["Cameras"].actions()[-1].isSeparator()
    calibration = next(
        action for action in menus["Instruments"].actions() if action.objectName() == "ct400EtaCalibrationAction"
    )
    assert calibration.text() == "CT400 ETA Calibration…"
    assert window.log_console_action.shortcut() == QKeySequence("F12")
    assert window.log_console_action.isCheckable()
    window.close()


def test_application_close_resumes_after_pm5020_worker_thread_finishes(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "pm-close.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    window.pm5020_panel._connect()
    qtbot.waitUntil(lambda: window.pm5020_panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert not window.close()
    assert window._pending_pm5020_close
    qtbot.waitUntil(lambda: window.pm5020_panel.shutdown_finished, timeout=2000)
    qtbot.waitUntil(lambda: not window._pending_pm5020_close, timeout=2000)


def test_pm5020_menu_owns_simulator_connect_and_disconnect(qtbot, monkeypatch, tmp_path):
    from PySide6.QtGui import QIcon

    connect_icon = QIcon(":/icons/connect.svg").pixmap(18, 18).toImage()
    disconnect_icon = QIcon(":/icons/disconnect.svg").pixmap(18, 18).toImage()
    assert not connect_icon.isNull() and not disconnect_icon.isNull()
    assert connect_icon != disconnect_icon

    window = _make_window(
        qtbot,
        monkeypatch,
        AppSettings(QSettings(str(tmp_path / "pm-menu.ini"), QSettings.Format.IniFormat)),
    )
    action = window.pm5020_connect_action
    panel = window.pm5020_panel
    assert panel is not None
    assert action.text() == "Connect PM5020"
    assert action.isCheckable()
    assert not action.isChecked()
    assert action.icon().pixmap(18, 18).toImage() == connect_icon
    assert panel.backend_mode == "simulation"
    assert panel.mode_label.text() == "Simulation — readings are simulated"
    assert not hasattr(panel, "connect_button") and not hasattr(panel, "disconnect_button")

    action.trigger()
    assert action.text() == "Connecting PM5020…"
    assert not action.isEnabled()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert action.text() == "Disconnect PM5020"
    assert action.isChecked()
    assert action.icon().pixmap(18, 18).toImage() == disconnect_icon
    backend = panel.worker._backend
    assert isinstance(backend, DummyPM5020)

    action.trigger()
    assert action.text() == "Disconnecting PM5020…"
    assert not action.isEnabled()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    assert action.text() == "Connect PM5020"
    assert not action.isChecked()
    assert action.icon().pixmap(18, 18).toImage() == connect_icon
    assert panel.worker._backend is backend
    window.close()


def test_pm5020_menu_recovers_after_connection_failure_and_repeated_action(qtbot, monkeypatch, tmp_path):
    import threading

    entered = threading.Event()
    release = threading.Event()
    instances = []

    class PendingMeter(DummyPM5020):
        def connect(self, resource_name=None):
            entered.set()
            assert release.wait(timeout=2)
            super().connect(resource_name)

    window = _make_window(
        qtbot,
        monkeypatch,
        AppSettings(QSettings(str(tmp_path / "pm-pending.ini"), QSettings.Format.IniFormat)),
    )
    panel = window.pm5020_panel
    assert panel is not None
    panel.worker._simulator_factory = lambda: instances.append(PendingMeter()) or instances[-1]
    action = window.pm5020_connect_action
    action.trigger()
    qtbot.waitUntil(entered.is_set, timeout=2000)
    assert action.text() == "Connecting PM5020…"
    window._handle_pm5020_connect_action_triggered()
    assert len(instances) == 1
    release.set()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert action.text() == "Disconnect PM5020"

    action.trigger()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)

    class FailedMeter(DummyPM5020):
        def connect(self, resource_name=None):
            raise RuntimeError("simulated connection failure")

    panel.worker._simulator_factory = FailedMeter
    action.trigger()
    qtbot.waitUntil(lambda: "connection failed" in panel.error_label.text().lower(), timeout=2000)
    assert panel._backend_state is PM5020State.DISCONNECTED
    assert action.text() == "Connect PM5020"
    assert action.isEnabled()
    window.close()


def test_pm5020_menu_shows_recovery_state_from_worker_backend(qtbot, monkeypatch, tmp_path):
    window = _make_window(
        qtbot,
        monkeypatch,
        AppSettings(QSettings(str(tmp_path / "pm-recovery.ini"), QSettings.Format.IniFormat)),
    )
    panel = window.pm5020_panel
    assert panel is not None
    panel.worker._simulator_factory = lambda: DummyPM5020(fail_operations={"read_power"})
    action = window.pm5020_connect_action
    action.trigger()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    backend = panel.worker._backend
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel._start()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.ERROR, timeout=2000)
    assert action.text() == "Disconnect PM5020 (Recovery)"
    assert action.isChecked()
    assert not action.icon().isNull()
    assert action.isEnabled()
    action.trigger()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    assert action.text() == "Connect PM5020"
    assert panel.worker._backend is backend
    window.close()


def test_physical_pm5020_is_never_connected_automatically_or_replaced_by_simulation(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "pm-physical.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    config = AppConfig.from_ini_dict(
        {
            "Instruments": {
                "ct400_backend": "simulation",
                "pm5020_backend": "physical",
                "pm5020_resource_name": "USB0::0x1313::0x80BB::M01213392::INSTR",
            }
        }
    )
    window = MainWindow(config, settings=settings)
    qtbot.addWidget(window)
    panel = window.pm5020_panel
    assert panel is not None
    assert not panel.task.worker_thread.isRunning()
    assert panel._backend_state is PM5020State.DISCONNECTED
    assert panel.mode_label.text() == "Physical PM5020"
    assert window.pm5020_connect_action.text() == "Connect PM5020"

    window.pm5020_connect_action.trigger()
    qtbot.waitUntil(lambda: "pm5020_wrapper_path" in panel.error_label.text(), timeout=2000)
    assert panel._backend_state is PM5020State.DISCONNECTED
    assert not isinstance(panel.worker._backend, DummyPM5020)
    assert window.pm5020_connect_action.text() == "Connect PM5020"
    window.close()


def test_pm5020_status_bar_shows_simulation_and_worker_lifecycle(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "pm-global-status.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    indicator = window.pm5020_status_label
    action = window.pm5020_connect_action
    assert indicator.objectName() == "pm5020StatusLabel"
    assert "Sim (Off)" in indicator.text()
    assert "Simulator" in indicator.toolTip()

    action.trigger()
    qtbot.waitUntil(lambda: window.pm5020_panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert indicator.text() == "PM5020: SIMULATED"
    qtbot.waitUntil(lambda: window.pm5020_panel._configuration_applied, timeout=2000)
    window.pm5020_panel._start()
    qtbot.waitUntil(lambda: window.pm5020_panel._monitor_session, timeout=2000)
    assert "SIMULATED" in indicator.text()
    action.trigger()
    qtbot.waitUntil(lambda: window.pm5020_panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    assert "Sim (Off)" in indicator.text()
    window.close()


def test_pm5020_status_formatter_maps_errors_and_physical_states():
    from ui.global_status import format_pm5020_status

    physical = format_pm5020_status(
        PM5020State.IDLE, activity="Connected / idle", simulated=False, resource="USB0::SERIAL::INSTR"
    )
    assert physical.text == "PM5020: Ready"
    assert "USB0::SERIAL::INSTR" in physical.tooltip
    assert physical.state == "ready"
    error = format_pm5020_status(PM5020State.ERROR, activity="Recovery required", simulated=False, resource="")
    assert error.state == "error"
    assert error.text == "PM5020: Error"
    simulated = format_pm5020_status(PM5020State.IDLE, activity="Monitoring", simulated=True, resource="")
    assert simulated.text == "PM5020: SIMULATED"


def test_edfa_menu_owns_connection_icons_and_status_bar(qtbot, monkeypatch, tmp_path):
    from PySide6.QtGui import QIcon

    from hardware.edfa_types import EDFAState

    window = _make_window(
        qtbot,
        monkeypatch,
        AppSettings(QSettings(str(tmp_path / "edfa-menu.ini"), QSettings.Format.IniFormat)),
    )
    action = window.edfa_connect_action
    panel = window.edfa_panel
    observation = window.edfa_observations_status_label
    assert action.objectName() == "edfaConnectAction"
    assert action.isCheckable() and not action.isChecked()
    assert action.text() == "Connect EDFA"
    assert action.icon().pixmap(18, 18).toImage() == QIcon(":/icons/connect.svg").pixmap(18, 18).toImage()
    assert not hasattr(panel, "connect_button") and not hasattr(panel, "disconnect_button")
    assert observation.parentWidget() is window.statusBar()
    assert observation.text() == "EDFA [SIM]: Disconnected"
    assert observation.toolTip().startswith(observation.text())

    action.trigger()
    assert action.text() == "Connecting EDFA…"
    assert not action.isEnabled()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.IDLE, timeout=3000)
    assert action.text() == "Disconnect EDFA"
    assert observation.text() == "EDFA [SIM]: Connected — awaiting telemetry"
    assert "SIM" in observation.text()
    assert "SIMULATED" in observation.toolTip()
    assert action.isChecked() and action.isEnabled()
    assert action.icon().pixmap(18, 18).toImage() == QIcon(":/icons/disconnect.svg").pixmap(18, 18).toImage()

    panel.request_read()
    qtbot.waitUntil(lambda: "Disconnected" not in observation.text(), timeout=3000)
    assert observation.text().startswith("EDFA [SIM]:")
    assert observation.toolTip().startswith(observation.text())
    window.statusBar().showMessage("CT400 operation", 1000)
    assert observation.text().startswith("EDFA [SIM]:")
    action.trigger()
    assert action.text() == "Disconnecting EDFA…"
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.DISCONNECTED, timeout=3000)
    assert action.text() == "Connect EDFA"
    assert not action.isChecked()
    assert observation.text() == "EDFA [SIM]: Disconnected"
    window.close()


def test_edfa_menu_cancelled_physical_confirmation_restores_icon(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    config = AppConfig(
        instruments={
            "ct400_backend": "simulation",
            "edfa_backend": "physical",
            "edfa_gpib_resource_name": "GPIB0::12::INSTR",
        }
    )
    settings = AppSettings(QSettings(str(tmp_path / "edfa-decline.ini"), QSettings.Format.IniFormat))
    window = MainWindow(config, settings=settings)
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    action = window.edfa_connect_action
    action.trigger()
    assert action.isEnabled() and not action.isChecked()
    assert action.text() == "Connect EDFA"
    assert not window.edfa_panel.task.worker_thread.isRunning()
    window.close()


def test_loading_defaults_error_uses_qt_status_bar(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "error.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)

    class InvalidDefaults:
        @property
        def scan_defaults(self):
            raise RuntimeError("simulated configuration read failure")

    original_config = window.config
    window.config = InvalidDefaults()
    try:
        window._load_defaults_from_config()
    finally:
        window.config = original_config

    assert window.statusBar().currentMessage() == "Error loading defaults from config"
    window.close()
