from config_model import AppConfig
from ui.control_panel import HistogramControlPanel, QMessageBox


def test_monitor_start_rejects_missing_ct400_when_connected_flag_is_true(qtbot, monkeypatch, caplog):
    panel = HistogramControlPanel(None, AppConfig())
    qtbot.addWidget(panel)
    panel.is_instrument_connected = True
    panel.ct400 = None
    warnings = []
    critical_messages = []
    operation_starts = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: critical_messages.append(args))
    panel.operation_started.connect(lambda: operation_starts.append(True))

    assert panel._apply_monitoring_settings() is False

    panel._start_monitoring()

    assert warnings == [(panel, "Not Connected", "CT400 device is not connected.")]
    assert critical_messages == []
    assert panel.monitoring is False
    assert panel._monitor_starting is False
    assert operation_starts == []
    assert panel.power_fetch_worker.ct400 is None
    assert not panel.timer.isActive()
    assert "CT400 device reference is unavailable" in caplog.text
