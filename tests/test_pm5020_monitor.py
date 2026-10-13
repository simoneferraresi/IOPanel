from __future__ import annotations

import math
import threading
import time

import pytest
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

    panel.disconnect_requested.emit()
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
