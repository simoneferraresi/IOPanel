"""Hardware independent regression coverage for R08 piezo ownership."""

from config_model import AppConfig
from hardware.dummy_ct400 import DummyCT400
from ui import main_window as main_window_module
from ui.alignment_panel import AlignmentPanel
from ui.main_window import CT400OperationState


class FakePiezo:
    def __init__(self, connected=False):
        self.connected = connected

    def is_connected(self):
        return self.connected


def _window(qtbot, monkeypatch):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    return window


def _set_ready_hardware(window):
    window.ct400_device = DummyCT400()
    window.piezo_left = FakePiezo(connected=True)
    window.piezo_right = FakePiezo(connected=True)


def test_missing_piezo_menu_actions_start_truthfully_disabled(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)

    assert window.piezo_connect_left_action.text() == "Left Piezo (Not Found)"
    assert not window.piezo_connect_left_action.isEnabled()
    assert window.piezo_connect_right_action.text() == "Right Piezo (Not Found)"
    assert not window.piezo_connect_right_action.isEnabled()


def test_connected_left_controller_is_retained_during_discovery(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    old_left, candidate = FakePiezo(connected=True), FakePiezo()
    window.piezo_left = old_left

    window._on_piezos_initialized(candidate, None)

    assert window.piezo_left is old_left
    assert window.piezo_connect_left_action.text() == "Disconnect Left Piezo"


def test_connected_right_controller_survives_missing_rediscovery(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    old_right = FakePiezo(connected=True)
    window.piezo_right = old_right

    window._on_piezos_initialized(None, None)

    assert window.piezo_right is old_right
    assert window.piezo_connect_right_action.text() == "Disconnect Right Piezo"


def test_connected_right_controller_is_retained_during_candidate_discovery(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    old_right, candidate = FakePiezo(connected=True), FakePiezo()
    window.piezo_right = old_right

    window._on_piezos_initialized(None, candidate)

    assert window.piezo_right is old_right
    assert window.piezo_connect_right_action.text() == "Disconnect Right Piezo"


def test_connected_left_controller_survives_missing_rediscovery(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    old_left = FakePiezo(connected=True)
    window.piezo_left = old_left

    window._on_piezos_initialized(None, None)

    assert window.piezo_left is old_left
    assert window.piezo_connect_left_action.text() == "Disconnect Left Piezo"


def test_disconnected_controller_is_replaced_or_cleared_by_discovery(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    old_left, candidate = FakePiezo(), FakePiezo()
    window.piezo_left = old_left
    window._on_piezos_initialized(candidate, None)
    assert window.piezo_left is candidate

    window._on_piezos_initialized(None, None)
    assert window.piezo_left is None


def test_disconnected_discovery_does_not_enable_alignment(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.ct400_device = DummyCT400()

    window._on_piezos_initialized(FakePiezo(), FakePiezo())

    assert not window.alignment_tab._hardware_ready


def test_both_connected_current_controllers_make_alignment_ready(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._sync_alignment_hardware()

    assert window.alignment_tab._hardware_ready
    assert window.alignment_tab.alignment_worker.piezo_left is window.piezo_left
    assert window.alignment_tab.alignment_worker.piezo_right is window.piezo_right
    window.alignment_tab.cleanup()


def test_alignment_worker_rebinds_to_changed_hardware(qtbot):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    old = (DummyCT400(), FakePiezo(True), FakePiezo(True))
    new = (DummyCT400(), FakePiezo(True), FakePiezo(True))
    panel.set_hardware(*old)
    first_worker, first_thread = panel.alignment_worker, panel.worker_thread
    panel.set_hardware(*new)

    assert panel.alignment_worker is not first_worker
    assert panel.worker_thread is not first_thread
    assert panel.alignment_worker.ct400 is new[0]
    assert panel.alignment_worker.piezo_left is new[1]
    assert panel.alignment_worker.piezo_right is new[2]
    panel.cleanup()


def test_alignment_same_hardware_does_not_duplicate_worker(qtbot):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    hardware = (DummyCT400(), FakePiezo(True), FakePiezo(True))
    panel.set_hardware(*hardware)
    worker, thread = panel.alignment_worker, panel.worker_thread
    panel.set_hardware(*hardware)
    assert panel.alignment_worker is worker
    assert panel.worker_thread is thread
    panel.cleanup()


def test_clear_hardware_detaches_worker_and_is_repeatable(qtbot):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    panel.set_hardware(DummyCT400(), FakePiezo(True), FakePiezo(True))

    panel.clear_hardware()
    panel.clear_hardware()

    assert panel.ct400 is None
    assert panel.piezo_left is None
    assert panel.piezo_right is None
    assert panel.alignment_worker is None
    assert panel.worker_thread is None
    assert not panel._hardware_ready
    assert panel._active_mode is None


def test_disconnect_start_disables_alignment_before_worker_starts(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._sync_alignment_hardware()
    started = []
    monkeypatch.setattr(main_window_module.QThreadPool.globalInstance(), "start", lambda worker: started.append(worker))

    window.connect_piezo("left")

    assert not window.alignment_tab._hardware_ready
    assert started
    assert "left" in window._piezo_operations_in_flight
    assert not window.piezo_connect_left_action.isEnabled()
    assert window.piezo_connect_left_action.text() == "Disconnecting Left..."


def test_refresh_and_direct_discovery_block_during_piezo_operation(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window._piezo_operations_in_flight.add("right")
    calls = []
    monkeypatch.setattr(window, "_init_ct400_lazy", lambda: calls.append("ct400"))

    window._on_refresh_instruments_triggered()
    assert window.piezo_task is None
    window._init_piezos_lazy()

    assert calls == ["ct400"]
    assert window.piezo_task is None


def test_successful_disconnect_clears_alignment_worker_ownership(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._sync_alignment_hardware()
    old_worker = window.alignment_tab.alignment_worker
    window.piezo_left.connected = False

    window._on_piezo_disconnection_success("left")

    assert not window.alignment_tab._hardware_ready
    assert window.alignment_tab.alignment_worker is None
    assert window.alignment_tab.worker_thread is None
    assert old_worker.piezo_left is not window.alignment_tab.piezo_left


def test_failed_disconnect_restores_readiness_from_actual_state(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._sync_alignment_hardware()
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *_args: None)

    window._piezo_operations_in_flight.add("left")
    window.alignment_tab.clear_hardware()
    window._on_piezo_connection_failed("left", "fake disconnect failure")

    assert window.alignment_tab._hardware_ready
    assert window.piezo_connect_left_action.text() == "Disconnect Left Piezo"
    window.alignment_tab.cleanup()


def test_connection_failure_ui_uses_actual_connected_state(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.piezo_left = FakePiezo(connected=True)
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *_args: None)
    window._piezo_operations_in_flight.add("left")

    window._on_piezo_connection_failed("left", "operation failed")

    assert window.piezo_connect_left_action.text() == "Disconnect Left Piezo"
    assert window.piezo_connect_left_action.isEnabled()


def test_active_alignment_blocks_panel_replacement(qtbot):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    old = (DummyCT400(), FakePiezo(True), FakePiezo(True))
    new = (DummyCT400(), FakePiezo(True), FakePiezo(True))
    panel.set_hardware(*old)
    worker = panel.alignment_worker
    panel._active_mode = "mapping"

    panel.set_hardware(*new)

    assert panel.alignment_worker is worker
    assert panel.ct400 is old[0]
    panel._active_mode = None
    panel.cleanup()


def test_refresh_and_connection_are_blocked_during_active_alignment(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._ct400_operation_state = CT400OperationState.ALIGNMENT
    calls = []
    monkeypatch.setattr(window, "_init_ct400_lazy", lambda: calls.append("ct400"))

    window._on_refresh_instruments_triggered()
    window._init_piezos_lazy()
    window.connect_piezo("left")

    assert calls == []
    assert window._piezo_operations_in_flight == set()


def test_ct400_replacement_rebinds_alignment_worker(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._on_ct400_initialized(window.ct400_device)
    old_worker = window.alignment_tab.alignment_worker
    replacement = DummyCT400()

    window._on_ct400_initialized(replacement)

    assert window.alignment_tab.alignment_worker is not old_worker
    assert window.alignment_tab.alignment_worker.ct400 is replacement
    window.alignment_tab.cleanup()
