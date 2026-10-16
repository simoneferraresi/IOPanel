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
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    assert created_on and created_on[0] != threading.get_ident()
    panel.interval.setText("0.25")
    panel._start()
    qtbot.waitUntil(lambda: readings.count() >= 2, timeout=3000)
    session_id = panel._monitor_session_id
    initial_history = len(panel.ch1._rows)
    panel.interval.setText("0.5")
    panel.interval.editingFinished.emit()
    qtbot.waitUntil(lambda: panel._acknowledged_interval_ms == 500, timeout=2000)
    assert panel._monitor_session_id == session_id
    assert len(panel.ch1._rows) >= initial_history
    assert panel.worker._timer is not None
    assert panel.worker._timer.thread() == panel.task.worker_thread
    latest_interval_request = panel._interval_request_id
    panel.interval.setText("0.1")
    panel.interval.editingFinished.emit()
    panel.interval_unit.setCurrentText("ms")
    assert panel._interval_request_id == latest_interval_request
    assert panel._acknowledged_interval_ms == 500
    panel.interval.setText("1500")
    panel.interval.editingFinished.emit()
    qtbot.waitUntil(lambda: panel._acknowledged_interval_ms == 1500, timeout=2000)
    assert panel.ch1.reading_label.text() == "10"
    assert panel.ch2.reading_label.text() == "800"
    assert panel.ch1.curve.getData()[1][-1] == pytest.approx(10)
    assert panel.ch2.curve.getData()[1][-1] == pytest.approx(800)
    assert panel.ch1.plot.getAxis("left").labelText == "Optical power"
    assert panel.ch1.plot.getAxis("left").labelUnits == "mW"

    assert panel.pause_resume_button.text() == "Pause"
    panel.pause_resume_button.click()
    qtbot.waitUntil(lambda: panel._paused, timeout=2000)
    assert panel.pause_resume_button.text() == "Resume"
    interval_request_count = panel._interval_request_id
    panel.interval.setText("2000")
    panel.interval.editingFinished.emit()
    assert panel._interval_request_id == interval_request_count
    resume_requests = QSignalSpy(panel.resume_requested)
    panel.pause_resume_button.click()
    qtbot.waitUntil(lambda: not panel._paused and readings.count() >= 4, timeout=2000)
    assert resume_requests.count() == 1
    assert resume_requests.at(0)[0] == 2000
    assert panel.pause_resume_button.text() == "Pause"
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
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.interval.setText("0.25")
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
    assert panel.mode_label.text() == "Simulation — readings are simulated"
    assert not hasattr(panel, "connect_button")
    assert not hasattr(panel, "disconnect_button")
    assert panel.start_button.isEnabled() is False
    assert math.isclose(float(panel.ch1.wavelength.text()), 1550)
    assert math.isclose(float(panel.ch2.wavelength.text()), 775)


def test_invalid_physical_configuration_restores_menu_operation_state(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(
        worker=PM5020MonitorWorker(),
        backend_mode="physical",
        resource_name="",
    )
    qtbot.addWidget(panel)
    panel.request_connect()
    qtbot.waitUntil(lambda: panel._activity == "Disconnected", timeout=2000)
    assert panel._backend_state is PM5020State.DISCONNECTED
    assert not panel._connection_request_pending
    assert "resource identifier" in panel.error_label.text()
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)


def test_configuration_is_automatic_and_latest_revision_is_acknowledged(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(worker=PM5020MonitorWorker())
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert not panel.start_button.isEnabled()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    assert not hasattr(panel, "apply_button")
    assert panel.start_button.isEnabled()
    panel.ch1.wavelength.setText("1549")
    panel.ch1.wavelength.editingFinished.emit()
    assert not panel._configuration_applied
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    assert panel.start_button.isEnabled()
    panel.ch1.wavelength.setText("1548")
    panel.ch1.wavelength.editingFinished.emit()
    assert not panel.start_button.isEnabled()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.ch1.unit.setCurrentText("W")
    assert panel._configuration_applied
    panel._disconnect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=2000)
    assert not panel._configuration_applied
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
    assert not panel.start_button.isEnabled()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)


def test_start_ack_clears_only_new_session_and_pause_resume_preserves_history(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    panel = PM5020Panel(worker=PM5020MonitorWorker())
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=2000)
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


def test_pm5020_layout_matches_control_column_and_plot_conventions(qtbot):
    from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLineEdit, QSizePolicy

    panel = PM5020Panel()
    qtbot.addWidget(panel)
    assert isinstance(panel.layout(), QHBoxLayout)
    panel.resize(1280, 720)
    panel.show()
    qtbot.wait(50)
    assert panel.controls_scroll.objectName() == "pm5020ControlsScroll"
    assert panel.controls_scroll.minimumWidth() == 320
    assert panel.controls_scroll.maximumWidth() == 400
    assert panel.plot_container.objectName() == "pm5020PlotsContainer"
    settings = panel.controls_scroll.widget()
    assert settings is not None
    assert settings.findChild(QGroupBox, "pm5020InstrumentStatusGroup") is None
    assert settings.findChild(QGroupBox, "pm5020OperationGroup") is not None
    assert settings.objectName() == "pm5020ControlsContent"
    assert settings.styleSheet() == ""
    assert settings.palette().color(settings.backgroundRole()).name() == "#ffffff"
    assert panel.error_label.parentWidget() is settings
    assert panel.ch1.parentWidget() is settings
    assert panel.ch2.parentWidget() is settings
    assert panel.ch1.plot_host.parentWidget() is panel.plot_container
    assert panel.ch2.plot_host.parentWidget() is panel.plot_container
    assert panel.ch1.plot.parentWidget() is panel.ch1.plot_host
    assert panel.ch2.plot.parentWidget() is panel.ch2.plot_host
    assert isinstance(panel.ch1.wavelength, QLineEdit)
    assert isinstance(panel.ch1.manual_range, QLineEdit)
    assert isinstance(panel.interval, QLineEdit)
    assert panel.mode_label.isHidden()
    assert not hasattr(panel.ch1, "measurement_status")
    assert not hasattr(panel.ch1, "age_label")
    assert not hasattr(panel, "advanced_toggle")
    assert not hasattr(panel, "advanced_settings")
    assert panel.ch1.enabled.text() == "CH1 — Optical Power"
    assert panel.ch2.enabled.text() == "CH2 — Optical Power"
    assert panel.ch1.enabled.parentWidget() is panel.ch1
    assert panel.ch1.input_filter.parentWidget() is panel.ch1.plot_actions
    assert panel.ch1.input_filter.isCheckable()
    assert not panel.ch1.input_filter.icon().isNull()
    assert "input filter / smoothing" in panel.ch1.input_filter.toolTip()
    assert panel.ch1.input_filter.objectName() == "pm5020Ch1InputFilterButton"
    assert panel.ch1.wavelength.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.ch1.manual_range.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.interval.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.ch1.reading_label.font().pointSize() == 20
    assert panel.ch2.reading_label.font().pointSize() == 20
    assert panel.ch1.reading_group.objectName() == "pm5020Ch1ReadingGroup"
    assert panel.ch2.reading_group.objectName() == "pm5020Ch2ReadingGroup"
    for channel in (panel.ch1, panel.ch2):
        assert abs(channel.reading_group.geometry().center().x() - channel._reading_row.geometry().center().x()) <= 3
    assert panel.start_button.objectName() == "pm5020StartButton"
    assert panel.stop_button.objectName() == "pm5020StopButton"
    assert panel.start_button.minimumHeight() >= 35
    assert panel.stop_button.minimumHeight() >= 35
    assert not hasattr(panel, "pause_button")
    assert not hasattr(panel, "resume_button")
    assert panel.pause_resume_button.text() == "Pause"
    assert not hasattr(panel, "connect_button")
    assert not hasattr(panel, "disconnect_button")
    assert not hasattr(panel, "apply_button")
    assert not panel.task.worker_thread.isRunning()
    panel.close()


def test_display_units_match_initial_axes_and_rescale_existing_data(qtbot):
    from hardware.pm5020_types import PowerReading

    panel = PM5020Panel()
    qtbot.addWidget(panel)
    assert panel.ch1.plot.getAxis("left").labelUnits == "mW"
    assert panel.ch2.plot.getAxis("left").labelUnits == "nW"

    panel._on_reading(PowerReading.now(PowerChannel.CH1, 0.002), 0.3)
    panel._on_reading(PowerReading.now(PowerChannel.CH2, 0.000000008), 0.3)
    assert panel.ch1.reading_label.text() == "2"
    assert panel.ch2.reading_label.text() == "8"
    assert panel.ch1.curve.getData()[1][-1] == pytest.approx(2)
    assert panel.ch2.curve.getData()[1][-1] == pytest.approx(8)

    panel.ch1.unit.setCurrentText("µW")
    assert panel.ch1.plot.getAxis("left").labelUnits == "µW"
    assert panel.ch1.reading_label.text() == "2e+03"
    assert panel.ch1.curve.getData()[1][-1] == pytest.approx(2000)
    assert panel.ch2.plot.getAxis("left").labelUnits == "nW"
    assert panel.ch2.curve.getData()[1][-1] == pytest.approx(8)
    assert panel.ch1._rows[-1][1] == pytest.approx(0.002)
    panel.close()


def test_channel_clear_erasers_do_not_clear_the_other_channel(qtbot):
    from hardware.pm5020_types import PowerReading

    panel = PM5020Panel()
    qtbot.addWidget(panel)
    panel._on_reading(PowerReading.now(PowerChannel.CH1, 0.001), 1)
    panel._on_reading(PowerReading.now(PowerChannel.CH2, 1e-8), 1)
    panel.ch1.clear_button.click()
    assert not panel.ch1._rows
    assert panel.ch1.reading_label.text() == "—"
    assert len(panel.ch2._rows) == 1
    assert panel.ch2.reading_label.text() == "10"
    assert panel.ch1.plot.getAxis("bottom").labelUnits == "s"
    assert panel.ch2.plot.getAxis("bottom").labelUnits == "s"
    assert panel.ch1.plot.viewRange()[0] == pytest.approx(panel.ch2.plot.viewRange()[0])
    panel.close()


def test_manual_power_range_unit_switch_preserves_watts_and_requires_valid_range(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    ch = panel.ch1
    ch.auto_range.setChecked(False)
    assert ch.channel_config().power_range_w == pytest.approx(0.001)
    ch.range_unit.setCurrentText("µW")
    assert float(ch.manual_range.text()) == pytest.approx(1000)
    assert ch.channel_config().power_range_w == pytest.approx(0.001)
    ch.manual_range.setText("5")
    assert ch.channel_config().power_range_w == pytest.approx(5e-6)
    ch.manual_range.setText("0")
    assert not ch.manual_range.hasAcceptableInput()
    with pytest.raises(ValueError, match="valid manual power range"):
        ch.channel_config()
    ch.manual_range.setText("5")
    ch.wavelength.setText("")
    with pytest.raises(ValueError, match="valid wavelength"):
        ch.channel_config()
    ch.wavelength.setText("775")
    assert ch.channel_config().wavelength_nm == pytest.approx(775)
    panel.close()


def test_update_every_units_preserve_requested_interval(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    assert panel._interval_ms() == 1000
    panel.interval_unit.setCurrentText("ms")
    assert float(panel.interval.text()) == pytest.approx(1000)
    assert panel._interval_ms() == 1000
    panel.interval.setText("250")
    assert panel._interval_ms() == 250
    panel.interval_unit.setCurrentText("s")
    assert float(panel.interval.text()) == pytest.approx(0.25)
    assert panel._interval_ms() == 250
    panel.close()


def test_update_interval_validates_effective_250_to_5000_ms_range(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    panel.interval_unit.setCurrentText("ms")
    assert panel.interval.hasAcceptableInput()
    panel.interval.setText("200")
    assert not panel.interval.hasAcceptableInput()
    assert not panel.start_button.isEnabled()
    panel.interval.setText("5000")
    assert panel.interval.hasAcceptableInput()
    assert panel._interval_ms() == 5000
    panel.interval_unit.setCurrentText("s")
    assert panel.interval.text() == "5"
    assert panel._interval_ms() == 5000
    panel.close()


def test_plot_filter_buttons_toggle_per_channel_configuration(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    assert not panel.ch1.input_filter.isChecked()
    assert not panel.ch2.input_filter.isChecked()
    panel.ch1.input_filter.click()
    assert panel.ch1.channel_config().input_filter_enabled
    assert not panel.ch2.channel_config().input_filter_enabled
    panel.ch1.input_filter.click()
    assert not panel.ch1.channel_config().input_filter_enabled
    panel.close()


def test_control_column_fits_without_vertical_scrolling_at_typical_height(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    panel.resize(1280, 720)
    panel.show()
    qtbot.wait(50)
    assert panel.controls_scroll.verticalScrollBar().maximum() == 0
    panel.close()


def test_connected_configuration_rejects_invalid_and_both_disabled_settings(qtbot):
    from logic.pm5020_monitor import PM5020MonitorWorker

    meter = DummyPM5020()
    panel = PM5020Panel(worker=PM5020MonitorWorker(simulator_factory=lambda: meter))
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    previous_configs = dict(meter._channel_configs)

    panel.ch1.wavelength.setText("")
    panel.ch1.wavelength.editingFinished.emit()
    qtbot.waitUntil(lambda: "Invalid channel settings" in panel.error_label.text(), timeout=1000)
    assert meter._channel_configs == previous_configs

    panel.ch1.wavelength.setText("1550")
    panel.ch1.wavelength.editingFinished.emit()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=2000)
    panel.ch1.enabled.setChecked(False)
    panel.ch2.enabled.setChecked(False)
    qtbot.waitUntil(lambda: "Enable at least one channel" in panel.error_label.text(), timeout=2000)
    assert not panel.start_button.isEnabled()
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)


def test_edit_during_inflight_configuration_applies_newest_revision(qtbot):
    entered = threading.Event()
    release = threading.Event()

    class BlockingConfigMeter(DummyPM5020):
        blocked = False

        def configure_channel(self, channel, config):
            if not self.blocked:
                self.blocked = True
                entered.set()
                assert release.wait(timeout=2)
            return super().configure_channel(channel, config)

    from logic.pm5020_monitor import PM5020MonitorWorker

    meter = BlockingConfigMeter()
    panel = PM5020Panel(worker=PM5020MonitorWorker(simulator_factory=lambda: meter))
    qtbot.addWidget(panel)
    panel._connect()
    qtbot.waitUntil(entered.is_set, timeout=2000)
    assert not panel.start_button.isEnabled()

    panel.ch1.wavelength.setText("1547")
    panel.ch1.wavelength.editingFinished.emit()
    latest_revision = panel._settings_revision
    release.set()
    qtbot.waitUntil(lambda: panel._configuration_applied, timeout=3000)
    assert panel._settings_revision == latest_revision
    assert meter._channel_configs[PowerChannel.CH1].wavelength_nm == pytest.approx(1547)
    assert panel.start_button.isEnabled()
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=2000)
