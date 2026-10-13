from __future__ import annotations

import math
import threading
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtTest import QSignalSpy

from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020_types import PM5020State, PowerChannel
from logic.pm5020_monitor import convert_power, format_power
from ui.pm5020_panel import PM5020Panel


@pytest.mark.parametrize(
    ("power_w", "unit", "expected"),
    [
        (1.0, "W", 1.0),
        (1e-3, "mW", 1.0),
        (1e-6, "µW", 1.0),
        (1e-9, "nW", 1.0),
        (1e-3, "dBm", 0.0),
        (1e-6, "dBm", -30.0),
        (1e-15, "nW", 1e-6),
        (1e100, "mW", 1e103),
    ],
)
def test_convert_power_uses_watts_as_backend_representation(power_w, unit, expected):
    assert convert_power(power_w, unit) == pytest.approx(expected)


@pytest.mark.parametrize("power_w", [0.0, -1.0, float("nan"), float("inf"), "invalid"])
def test_dbm_conversion_handles_nonpositive_and_invalid_values(power_w):
    assert convert_power(power_w, "dBm") is None
    assert format_power(power_w, "dBm") == "—"


def test_unknown_display_unit_is_rejected_without_reinterpreting_watts():
    assert convert_power(1.0, "mW-ish") is None


def test_simulator_panel_reads_channels_and_plots_independent_units(qtbot):
    created_on: list[int] = []

    def simulator_factory():
        created_on.append(threading.get_ident())
        return DummyPM5020(power_levels_w={PowerChannel.CH1: 0.01, PowerChannel.CH2: 8e-7})

    from logic.pm5020_monitor import PM5020MonitorWorker

    worker = PM5020MonitorWorker(simulator_factory=simulator_factory)
    panel = PM5020Panel(worker=worker)
    qtbot.addWidget(panel)
    readings = QSignalSpy(worker.reading_ready)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=3000)
    assert created_on and created_on[0] != threading.get_ident()
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.interval.setValue(250)
    panel._start()
    qtbot.waitUntil(lambda: readings.count() >= 2, timeout=3000)
    assert panel.ch1.reading_label.text() == "10 mW"
    assert panel.ch2.reading_label.text() == "800 nW"
    assert panel.ch1.curve.getData()[1][-1] == pytest.approx(10)
    assert panel.ch2.curve.getData()[1][-1] == pytest.approx(800)
    assert panel.ch1.plot.getAxis("left").labelText == "Optical power"
    assert panel.ch1.plot.getAxis("left").labelUnits == "mW"

    panel.pause_requested.emit()
    qtbot.waitUntil(lambda: panel._paused, timeout=2000)
    panel.resume_requested.emit(250)
    qtbot.waitUntil(lambda: not panel._paused and readings.count() >= 4, timeout=2000)
    panel.stop_requested.emit()
    qtbot.waitUntil(lambda: not panel._monitor_session, timeout=2000)
    panel._clear_history()
    assert not panel.ch1._rows and not panel.ch2._rows

    panel._disconnect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=3000)


def test_slow_simulated_reads_are_serialized_and_gui_remains_responsive(qtbot):
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    class SlowMeter(DummyPM5020):
        def read_power(self, channel):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.04)
                return super().read_power(channel)
            finally:
                with active_lock:
                    active -= 1

    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(worker=PM5020MonitorWorker(simulator_factory=SlowMeter))
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=3000)
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.interval.setValue(250)
    panel._start()
    handled: list[bool] = []
    from PySide6.QtCore import QTimer

    QTimer.singleShot(5, lambda: handled.append(True))
    qtbot.waitUntil(lambda: bool(handled) and bool(panel.ch2._rows), timeout=3000)
    assert max_active == 1
    assert len(panel.ch1._rows) <= 2400
    assert len(panel.ch2._rows) <= 2400
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=3000)


def test_shutdown_waits_for_blocked_fake_vendor_read_without_blocking_gui(qtbot):
    entered = threading.Event()
    release_read = threading.Event()

    class BlockingMeter(DummyPM5020):
        def read_power(self, channel):
            entered.set()
            release_read.wait(timeout=2)
            return super().read_power(channel)

    from PySide6.QtCore import QTimer

    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(worker=PM5020MonitorWorker(simulator_factory=BlockingMeter))
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=3000)
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel._start()
    gui_timer_ran: list[bool] = []
    worker_preserved_after_timeout: list[bool] = []
    panel._shutdown_timer.setInterval(30)
    panel.shutdown_timeout.connect(
        lambda _message: worker_preserved_after_timeout.append(panel.task.worker_thread.isRunning())
    )
    QTimer.singleShot(5, panel.request_shutdown)
    QTimer.singleShot(20, lambda: gui_timer_ran.append(True))
    QTimer.singleShot(120, release_read.set)
    qtbot.waitUntil(lambda: bool(gui_timer_ran) and panel.shutdown_finished, timeout=3000)
    assert entered.is_set()
    assert gui_timer_ran
    assert worker_preserved_after_timeout == [True]


def test_panel_does_not_start_or_connect_hardware_until_user_action(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    assert not panel.task.worker_thread.isRunning()
    assert panel._backend_state is PM5020State.DISCONNECTED
    assert panel.mode.currentText() == "Simulator"
    assert panel.start_button.isEnabled() is False
    assert math.isclose(panel.ch1.wavelength.value(), 1550)
    assert math.isclose(panel.ch2.wavelength.value(), 775)


def test_configuration_is_pending_until_ack_and_invalidated_by_edits_and_disconnect(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(worker=PM5020MonitorWorker())
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert not panel.start_button.isEnabled()
    panel._apply_configuration()
    assert not panel._configuration_applied
    assert panel._pending_configuration is not None
    first_request = panel._pending_configuration
    panel._apply_configuration()
    assert panel._pending_configuration == first_request
    panel.ch1.wavelength.setValue(1549)
    assert panel._pending_configuration is None
    assert not panel._configuration_applied
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    assert panel.apply_button.text() == "Settings Applied"
    assert panel.start_button.isEnabled()
    panel.ch1.wavelength.setValue(1548)
    assert not panel._configuration_applied
    assert not panel.start_button.isEnabled()
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.ch1.unit.setCurrentText("W")
    assert panel._configuration_applied
    panel._disconnect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    assert not panel._configuration_applied
    panel._on_configuration_result(first_request[0], first_request[1], first_request[2], True, "")
    assert not panel._configuration_applied
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert not panel.start_button.isEnabled()
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)


def test_start_ack_clears_only_new_session_and_pause_resume_preserves_history(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(worker=PM5020MonitorWorker())
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel._start()
    qtbot.waitUntil(lambda: panel._monitor_session and bool(panel.ch1._rows), timeout=2000)
    panel.pause_requested.emit()
    qtbot.waitUntil(lambda: panel._paused, timeout=2000)
    retained = len(panel.ch1._rows)
    panel.resume_requested.emit(250)
    qtbot.waitUntil(lambda: not panel._paused and len(panel.ch1._rows) > retained, timeout=2000)
    panel.stop_requested.emit()
    qtbot.waitUntil(lambda: not panel._monitor_session, timeout=2000)
    finished_history = len(panel.ch1._rows)
    assert finished_history > 0
    panel._start()
    qtbot.waitUntil(lambda: panel._monitor_session and bool(panel.ch1._rows), timeout=2000)
    assert panel.ch1._rows[0][0] < 0.1
    panel._clear_history()
    assert not panel.ch1._rows and panel.ch1.reading_label.text() == "—"
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)


def test_thread_finished_callback_runs_on_gui_thread_and_is_idempotent(qtbot):
    class TrackingPanel(PM5020Panel):
        callback_threads: list[QThread]

        def __init__(self):
            self.callback_threads = []
            super().__init__()

        def _on_thread_finished(self):
            self.callback_threads.append(QThread.currentThread())
            super()._on_thread_finished()

    panel = TrackingPanel()
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    panel._shutdown_timer.start(2000)
    completed = QSignalSpy(panel.shutdown_complete)
    panel.request_shutdown()
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)
    assert len(panel.callback_threads) == 1
    assert panel.callback_threads[0] == QThread.currentThread()
    assert not panel._shutdown_timer.isActive()
    assert completed.count() == 1


def test_configuration_failure_and_stop_preserve_recovery_state(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    class FailSecondChannel(DummyPM5020):
        config_calls = 0

        def configure_channel(self, channel, config):
            self.config_calls += 1
            if self.config_calls == 2:
                self._set_state(PM5020State.ERROR)
                raise RuntimeError("simulated CH2 configuration failure")
            return super().configure_channel(channel, config)

    meter = FailSecondChannel()
    panel = PM5020Panel(worker=PM5020MonitorWorker(simulator_factory=lambda: meter))
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    panel._apply_configuration()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.ERROR, timeout=2000)
    qtbot.waitUntil(lambda: panel._pending_configuration is None, timeout=2000)
    assert not panel._configuration_applied
    assert not panel.start_button.isEnabled()
    assert meter.config_calls == 2
    panel.stop_requested.emit()
    qtbot.waitUntil(lambda: panel._activity == "Recovery required", timeout=2000)
    assert panel._backend_state is PM5020State.ERROR
    panel.disconnect_requested.emit(panel._connection_generation + 1)
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    panel.stop_requested.emit()
    qtbot.waitUntil(lambda: panel._activity == "Disconnected", timeout=2000)
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)
