"""Hardware independent regression coverage for R08 piezo ownership."""

from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent, QIcon

from config_model import AppConfig
from hardware.dummy_ct400 import DummyCT400
from hardware.piezo import PiezoConnectionError, PiezoController
from ui import main_window as main_window_module
from ui.alignment_panel import AlignmentPanel
from ui.main_window import CT400OperationState


def test_explicit_piezo_connect_primes_dll_once_without_probing_other_ports(monkeypatch):
    calls = []

    class FakeDLL:
        def List(self, _buffer, size):
            calls.append(("List", size))
            return 2

        def Open(self, port, baud, timeout):
            calls.append(("Open", port, baud, timeout))
            return 0 if port == b"COM3" else 1

        def Close(self, handle):
            calls.append(("Close", handle))
            return 0

    dll = FakeDLL()
    monkeypatch.setattr(PiezoController, "_dll", dll)
    monkeypatch.setattr(PiezoController, "_primed_dll", None)
    left = PiezoController(Path("unused-test-only.dll"))
    right = PiezoController(Path("unused-test-only.dll"))

    # Creating the wrappers is passive. Only operator-requested Connect
    # performs native List/Open. Handle zero is valid, not disconnected.
    assert calls == []
    left.connect("COM3")
    right.connect("COM4")
    assert left.is_connected() and left.hdl == 0
    assert right.is_connected() and right.hdl == 1
    assert calls == [
        ("List", 10240),
        ("Open", b"COM3", 115200, 3),
        ("Open", b"COM4", 115200, 3),
    ]
    left.disconnect()
    right.disconnect()
    assert calls[-2:] == [("Close", 0), ("Close", 1)]
    assert not left.is_connected() and not right.is_connected()


def test_native_piezo_list_failure_blocks_open_and_leaves_device_disconnected(monkeypatch):
    calls = []

    class FakeDLL:
        def List(self, _buffer, _size):
            calls.append("List")
            return -5

        def Open(self, *_args):
            raise AssertionError("Open must not follow failed List")

    monkeypatch.setattr(PiezoController, "_dll", FakeDLL())
    monkeypatch.setattr(PiezoController, "_primed_dll", None)
    piezo = PiezoController(Path("unused-test-only.dll"))

    with pytest.raises(PiezoConnectionError, match="List failed before Connect with error code: -5"):
        piezo.connect("COM3")
    assert calls == ["List"]
    assert not piezo.is_connected()
    assert piezo.port is None


def test_native_piezo_open_failure_after_list_preserves_fail_closed_state(monkeypatch):
    calls = []

    class FakeDLL:
        def List(self, _buffer, _size):
            calls.append("List")
            return 2

        def Open(self, port, baud, timeout):
            calls.append(("Open", port, baud, timeout))
            return -1

    monkeypatch.setattr(PiezoController, "_dll", FakeDLL())
    monkeypatch.setattr(PiezoController, "_primed_dll", None)
    piezo = PiezoController(Path("unused-test-only.dll"))

    with pytest.raises(PiezoConnectionError, match="DLL error code: -1"):
        piezo.connect("COM3")
    assert calls == ["List", ("Open", b"COM3", 115200, 3)]
    assert not piezo.is_connected()
    assert piezo.port is None


class FakePiezo:
    def __init__(self, connected=False):
        self.connected = connected
        self.disconnect_calls = 0

    def is_connected(self):
        return self.connected

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False


class FakeNativeCT400:
    def __init__(self, fail_close=False):
        self.close_calls = 0
        self.laser_calls = []
        self.handle = 73
        self.fail_close = fail_close

    def close(self):
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("fake native close failure")

    def set_laser(self, *args, **kwargs):
        self.laser_calls.append((args, kwargs))


def _window(qtbot, monkeypatch):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    return window


def _set_ready_hardware(window):
    window.ct400_device = DummyCT400()
    window.piezo_left = FakePiezo(connected=True)
    window.piezo_right = FakePiezo(connected=True)


def _capture_final_cleanup(window, monkeypatch, calls):
    real_alignment_cleanup = window.alignment_tab.cleanup
    real_histogram_cleanup = window.histogram_control.cleanup_worker_thread

    def record_alignment_cleanup():
        calls.append("alignment")
        if window.alignment_tab.worker_thread is not None:
            real_alignment_cleanup()

    def record_histogram_cleanup():
        calls.append("histogram")
        real_histogram_cleanup()

    monkeypatch.setattr(window.alignment_tab, "cleanup", record_alignment_cleanup)
    monkeypatch.setattr(window.histogram_control, "cleanup_worker_thread", record_histogram_cleanup)
    monkeypatch.setattr(window, "_cleanup_cameras", lambda: calls.append("cameras"))
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: calls.append("vimba"))


def _begin_close_with_piezo_operations(window, monkeypatch, sides, ct400_device=None):
    scheduled = []
    calls = []
    _capture_final_cleanup(window, monkeypatch, calls)
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *_args: None)
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    window.ct400_device = ct400_device
    window.piezo_left = FakePiezo()
    window.piezo_right = FakePiezo()
    window._piezo_operations_in_flight = set(sides)
    window.show()

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert window.isVisible()
    assert window._pending_piezo_operation_close
    assert calls == []
    assert window.piezo_left.disconnect_calls == 0
    assert window.piezo_right.disconnect_calls == 0
    if ct400_device is not None:
        assert ct400_device.close_calls == 0
    return scheduled, calls


def _qt_thread_stopped(thread):
    if thread is None:
        return True
    try:
        return not thread.isRunning()
    except RuntimeError:
        return True


def _ensure_histogram_worker_stops(window, request, monkeypatch):
    thread = window.histogram_control.power_fetch_thread
    cleanup = window.histogram_control.cleanup_worker_thread

    def cleanup_once():
        if not _qt_thread_stopped(thread):
            cleanup()

    monkeypatch.setattr(window.histogram_control, "cleanup_worker_thread", cleanup_once)

    def cleanup_if_running():
        cleanup_once()

    request.addfinalizer(cleanup_if_running)
    return thread


def test_missing_piezo_menu_actions_start_truthfully_disabled(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)

    assert window.piezo_connect_left_action.text() == "Left Piezo (Not Found)"
    assert not window.piezo_connect_left_action.isEnabled()
    assert window.piezo_connect_right_action.text() == "Right Piezo (Not Found)"
    assert not window.piezo_connect_right_action.isEnabled()


def test_piezo_menu_icons_and_checkmarks_track_independent_real_connection_state(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    left = window.piezo_connect_left_action
    right = window.piezo_connect_right_action
    connect_icon = QIcon(":/icons/connect.svg").pixmap(18, 18).toImage()
    disconnect_icon = QIcon(":/icons/disconnect.svg").pixmap(18, 18).toImage()
    spinner_icon = QIcon(":/icons/spinner.svg").pixmap(18, 18).toImage()
    assert connect_icon != disconnect_icon
    assert left.isCheckable() and right.isCheckable()
    assert left.objectName() == "connectLeftPiezoAction"
    assert right.objectName() == "connectRightPiezoAction"
    assert not left.isChecked() and not right.isChecked()

    window._piezo_discovery_pending = True
    window._update_piezo_action("left")
    window._update_piezo_action("right")
    assert left.text() == "Finding Left Piezo..."
    assert right.text() == "Finding Right Piezo..."
    assert left.icon().pixmap(18, 18).toImage() == spinner_icon
    assert not left.isEnabled() and not right.isEnabled()

    window._on_piezos_initialized(FakePiezo(), FakePiezo())
    assert left.text() == "Connect Left Piezo"
    assert right.text() == "Connect Right Piezo"
    assert left.icon().pixmap(18, 18).toImage() == connect_icon
    assert right.icon().pixmap(18, 18).toImage() == connect_icon
    assert left.isEnabled() and right.isEnabled()

    window.piezo_left.connected = True
    window._update_piezo_action("left")
    assert left.text() == "Disconnect Left Piezo"
    assert left.isChecked() and not right.isChecked()
    assert left.icon().pixmap(18, 18).toImage() == disconnect_icon

    window._piezo_operations_in_flight.add("left")
    window._update_piezo_action("left")
    assert left.text() == "Disconnecting Left..."
    assert not left.isEnabled()
    assert left.isChecked()
    assert left.icon().pixmap(18, 18).toImage() == spinner_icon
    window._piezo_operations_in_flight.clear()
    window.piezo_left.connected = False
    window._update_piezo_action("left")
    assert left.text() == "Connect Left Piezo"
    assert not left.isChecked()


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


def test_rejected_ct400_initialization_closes_unadopted_native_device(qtbot, monkeypatch):
    monkeypatch.setattr(main_window_module, "CT400", FakeNativeCT400)
    window = _window(qtbot, monkeypatch)
    old_ct400 = FakeNativeCT400()
    replacement = FakeNativeCT400()
    _set_ready_hardware(window)
    window.ct400_device = old_ct400
    window.control_panel.set_instrument(old_ct400)
    window.histogram_control.set_instrument(old_ct400)
    window.alignment_tab.set_hardware(old_ct400, window.piezo_left, window.piezo_right)
    window._ct400_operation_state = CT400OperationState.ALIGNMENT

    try:
        window._on_ct400_initialized(replacement)

        assert window.ct400_device is old_ct400
        assert replacement.close_calls == 1
        assert replacement.laser_calls == []
        assert window.control_panel.ct400 is old_ct400
        assert window.histogram_control.ct400 is old_ct400
        assert window.alignment_tab.ct400 is old_ct400
        assert window.alignment_tab.alignment_worker.ct400 is old_ct400
    finally:
        window._ct400_operation_state = CT400OperationState.IDLE
        window.alignment_tab.cleanup()


def test_rejected_ct400_close_failure_keeps_current_ownership(qtbot, monkeypatch, caplog):
    monkeypatch.setattr(main_window_module, "CT400", FakeNativeCT400)
    window = _window(qtbot, monkeypatch)
    old_ct400 = FakeNativeCT400()
    replacement = FakeNativeCT400(fail_close=True)
    window.ct400_device = old_ct400
    window.control_panel.set_instrument(old_ct400)
    window.histogram_control.set_instrument(old_ct400)
    window.alignment_tab.ct400 = old_ct400
    window._ct400_operation_state = CT400OperationState.ALIGNMENT

    window._on_ct400_initialized(replacement)

    assert window.ct400_device is old_ct400
    assert replacement.close_calls == 1
    assert window.control_panel.ct400 is old_ct400
    assert window.histogram_control.ct400 is old_ct400
    assert window.alignment_tab.ct400 is old_ct400
    assert "Could not release rejected CT400 resources" in caplog.text


def test_close_waits_for_single_piezo_operation_then_runs_normal_cleanup(qtbot, monkeypatch, request):
    window = _window(qtbot, monkeypatch)
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    monkeypatch.setattr(main_window_module, "CT400", FakeNativeCT400)
    physical_ct400 = FakeNativeCT400()
    scheduled, calls = _begin_close_with_piezo_operations(window, monkeypatch, {"left"}, physical_ct400)

    window.piezo_left.connected = True
    window._on_piezo_connection_success("left")
    assert window._pending_piezo_operation_close
    assert len(scheduled) == 1
    assert scheduled[0][0] == 0
    assert scheduled[0][1].__self__ is window
    assert window.alignment_tab.alignment_worker is None

    scheduled[0][1]()

    assert calls == ["alignment", "histogram", "cameras", "vimba"]
    assert window.piezo_left.disconnect_calls == 1
    assert window.piezo_right.disconnect_calls == 0
    assert physical_ct400.close_calls == 1
    assert not window._pending_piezo_operation_close
    assert not window.isVisible()
    assert _qt_thread_stopped(histogram_thread)


def test_close_waits_for_all_piezo_operations_and_ignores_stale_completion(qtbot, monkeypatch, request):
    window = _window(qtbot, monkeypatch)
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    scheduled, calls = _begin_close_with_piezo_operations(window, monkeypatch, {"left", "right"})

    window.piezo_left.connected = True
    window._on_piezo_connection_success("left")
    assert window._pending_piezo_operation_close
    assert window._piezo_operations_in_flight == {"right"}
    assert scheduled == []
    assert calls == []
    assert window.alignment_tab.alignment_worker is None

    window._on_piezo_connection_success("left")
    assert scheduled == []

    window.piezo_right.connected = False
    window._on_piezo_disconnection_success("right")
    assert window._piezo_operations_in_flight == set()
    assert len(scheduled) == 1
    assert scheduled[0][0] == 0
    assert window.alignment_tab.alignment_worker is None

    window._on_piezo_disconnection_success("right")
    assert len(scheduled) == 1

    scheduled[0][1]()
    assert calls == ["alignment", "histogram", "cameras", "vimba"]
    assert not window._pending_piezo_operation_close
    assert not window.isVisible()
    assert _qt_thread_stopped(histogram_thread)


def test_pending_piezo_close_blocks_new_piezo_work(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window._pending_piezo_operation_close = True
    starts = []
    monkeypatch.setattr(main_window_module.TaskRunner, "start", lambda _task: starts.append("init"))
    monkeypatch.setattr(
        main_window_module.QThreadPool.globalInstance(), "start", lambda _worker: starts.append("worker")
    )
    window.piezo_left = FakePiezo()

    window.connect_piezo("left")
    window._on_refresh_instruments_triggered()
    window._init_piezos_lazy()

    assert starts == []
    assert window._piezo_operations_in_flight == set()
    assert window.piezo_task is None


@pytest.mark.parametrize("terminal", ["connect", "disconnect", "failure"])
def test_piezo_terminal_during_pending_close_does_not_rebuild_alignment(qtbot, monkeypatch, terminal):
    window = _window(qtbot, monkeypatch)
    _set_ready_hardware(window)
    window._sync_alignment_hardware()
    window._piezo_operations_in_flight.add("left")
    window._pending_piezo_operation_close = True
    scheduled = []
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *_args: None)
    window.alignment_tab.cleanup()
    assert window.alignment_tab.alignment_worker is None

    try:
        if terminal == "connect":
            window.piezo_left.connected = True
            window._on_piezo_connection_success("left")
        elif terminal == "disconnect":
            window.piezo_left.connected = False
            window._on_piezo_disconnection_success("left")
        else:
            window._on_piezo_connection_failed("left", "fake operation failure")

        assert window.alignment_tab.alignment_worker is None
        assert window.alignment_tab.worker_thread is None
        assert not window.alignment_tab._hardware_ready
        assert len(scheduled) == 1
    finally:
        window.alignment_tab.cleanup()


@pytest.mark.parametrize("failing_side", ["left", "right"])
@pytest.mark.parametrize("failure_stage", ["is_connected", "disconnect"])
def test_shutdown_disconnects_other_piezo_after_failure(
    qtbot, monkeypatch, request, caplog, failing_side, failure_stage
):
    """One controller's status/disconnect error must not strand the other or CT400."""
    window = _window(qtbot, monkeypatch)
    thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    monkeypatch.setattr(main_window_module, "CT400", FakeNativeCT400)
    native = FakeNativeCT400()
    window.ct400_device = native
    left, right = FakePiezo(connected=True), FakePiezo(connected=True)
    window.piezo_left, window.piezo_right = left, right
    failing = left if failing_side == "left" else right
    cleanup = []
    _capture_final_cleanup(window, monkeypatch, cleanup)

    if failure_stage == "is_connected":

        def fail_status():
            raise RuntimeError("simulated status failure")

        monkeypatch.setattr(failing, "is_connected", fail_status)
    else:

        def fail_disconnect():
            failing.disconnect_calls += 1
            raise RuntimeError("simulated disconnect failure")

        monkeypatch.setattr(failing, "disconnect", fail_disconnect)

    window.show()
    window.close()

    assert not window.isVisible()
    assert (left if failing_side == "right" else right).disconnect_calls == 1
    assert failing.disconnect_calls == (0 if failure_stage == "is_connected" else 1)
    assert native.close_calls == 1
    assert cleanup == ["alignment", "histogram", "cameras", "vimba"]
    assert f"Error disconnecting {failing_side} piezo hardware during shutdown" in caplog.text
    assert _qt_thread_stopped(thread)


def test_shutdown_phases_preserve_ui_workers_camera_piezos_ct400_vimba_order(qtbot, monkeypatch, request):
    """Native cleanup must remain after UI snapshot and the owner-thread gates."""
    window = _window(qtbot, monkeypatch)
    thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    monkeypatch.setattr(main_window_module, "CT400", FakeNativeCT400)
    events = []
    native = FakeNativeCT400()
    original_native_close = native.close

    def close_native():
        events.append("ct400")
        original_native_close()

    monkeypatch.setattr(native, "close", close_native)
    window.ct400_device = native

    class OrderedPiezo(FakePiezo):
        def __init__(self, side):
            super().__init__(connected=True)
            self.side = side

        def disconnect(self):
            events.append(self.side)
            super().disconnect()

    window.piezo_left = OrderedPiezo("left")
    window.piezo_right = OrderedPiezo("right")
    monkeypatch.setattr(window, "_save_ui_preferences", lambda: events.append("preferences"))
    real_panel_cleanup = window._cleanup_shutdown_panel_workers

    def cleanup_panels():
        events.append("workers")
        real_panel_cleanup()

    monkeypatch.setattr(window, "_cleanup_shutdown_panel_workers", cleanup_panels)
    monkeypatch.setattr(window, "_cleanup_cameras", lambda: events.append("cameras"))
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: events.append("vimba"))
    real_ui_finish = window._finish_shutdown_ui

    def finish_ui():
        events.append("ui-finished")
        real_ui_finish()

    monkeypatch.setattr(window, "_finish_shutdown_ui", finish_ui)

    window.show()
    window.close()

    assert events == [
        "preferences",
        "workers",
        "cameras",
        "left",
        "right",
        "ct400",
        "vimba",
        "ui-finished",
    ]
    assert not window.isVisible()
    assert native.close_calls == 1
    assert _qt_thread_stopped(thread)
