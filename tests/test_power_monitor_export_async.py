"""Power Monitor CSV/MAT worker and GUI lifetime regression tests, no hardware."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QDialog
from scipy.io import loadmat

from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_export import build_power_monitor_export_v1
from logic.power_monitor_export_worker import PowerMonitorExportWorker, _write_staged_power_monitor_format
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingStopReason,
)
from ui import main_window as main_window_module
from ui import plot_widgets
from ui.power_monitor_export_dialog import PowerMonitorExportRequest


def _recording():
    detectors = (Detector.DE_3, Detector.DE_1)
    settings = PowerMonitorAcquisitionSettings(1550.0, "1.0", "mW", 1.0, LaserInput.LI_1, detectors, 250)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    return PowerMonitorRecording(
        settings=settings,
        elapsed_s=np.array([0.1, 0.23, 0.44]),
        pout_data=np.array([-5.0, -4.5, -4.0]),
        detector_data=np.array([[3.0, np.nan, 5.0], [1.0, 2.0, 4.0]]),
        detectors=detectors,
        started_at_utc=now,
        completed_at_utc=now + timedelta(seconds=1),
        duration_s=1.0,
        backend="synthetic",
        simulated=True,
        stop_reason=PowerMonitorRecordingStopReason.USER_STOPPED,
    )


def _payload():
    return build_power_monitor_export_v1(_recording(), comment="line one\nline two")


def _dialog(monkeypatch, directory: Path, *, csv=True, mat=True, base_name="saved"):
    class Dialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *_args):
            self.export_request = PowerMonitorExportRequest(
                directory, base_name, csv, mat, "line one\nline two"
            )

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(plot_widgets, "PowerMonitorExportDialog", Dialog)


def _widget(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    widget = plot_widgets.PowerMonitorTraceWidget(settings)
    qtbot.addWidget(widget)
    widget.set_completed_recording(_recording())
    _dialog(monkeypatch, tmp_path)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *_args: None)
    return widget


def test_power_monitor_staged_export_preserves_format_and_cleans_files(tmp_path):
    worker = PowerMonitorExportWorker(
        _payload(), {"CSV": tmp_path / "saved.csv", "MAT": tmp_path / "saved.mat"}, frozenset()
    )
    outcomes = []
    worker.outcome.connect(outcomes.append)
    worker.run()
    assert len(outcomes) == 1 and outcomes[0].errors == ()
    assert {p.name for p in outcomes[0].saved} == {"saved.csv", "saved.mat"}
    csv_path = tmp_path / "saved.csv"
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "# SampleCount: 3" in csv_text
    assert "# ActiveDetectors: DE_3,DE_1" in csv_text
    assert "# Comment: \"line one\\nline two\"" in csv_text
    samples = np.loadtxt(csv_path, delimiter=",", comments="#")
    assert samples.shape == (3, 6)
    np.testing.assert_array_equal(samples[:, 0], [0.1, 0.23, 0.44])
    np.testing.assert_array_equal(samples[:, 2], [1.0, 2.0, 4.0])
    assert np.isnan(samples[1, 4])
    mat = loadmat(tmp_path / "saved.mat", simplify_cells=True)
    assert mat["comment"] == "line one\nline two"
    assert mat["active_detector_count"] == 2
    np.testing.assert_array_equal(mat["active_detector_ids"], [3, 1])
    assert {p.name for p in tmp_path.iterdir()} == {"saved.csv", "saved.mat"}


def test_failed_power_monitor_staging_keeps_old_bytes(monkeypatch, tmp_path):
    import logic.power_monitor_export_worker as worker_module

    target = tmp_path / "saved.csv"
    target.write_bytes(b"old-data")

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(worker_module.np, "savetxt", fail)
    with pytest.raises(OSError, match="disk full"):
        _write_staged_power_monitor_format("CSV", target, _payload(), overwrite_confirmed=True)
    assert target.read_bytes() == b"old-data"
    assert list(tmp_path.iterdir()) == [target]


def test_new_power_monitor_target_race_does_not_overwrite(monkeypatch, tmp_path):
    import logic.power_monitor_export_worker as worker_module

    target = tmp_path / "saved.csv"
    savetxt = worker_module.np.savetxt

    def racing(*args, **kwargs):
        target.write_bytes(b"created-after-confirmation")
        return savetxt(*args, **kwargs)

    monkeypatch.setattr(worker_module.np, "savetxt", racing)
    with pytest.raises(FileExistsError):
        _write_staged_power_monitor_format("CSV", target, _payload(), overwrite_confirmed=False)
    assert target.read_bytes() == b"created-after-confirmation"
    assert list(tmp_path.iterdir()) == [target]


def test_independent_power_monitor_formats_and_atomic_approved_overwrite(monkeypatch, tmp_path):
    import logic.power_monitor_export_worker as worker_module

    target = tmp_path / "saved.csv"
    target.write_bytes(b"previous")
    mat_path = tmp_path / "saved.mat"

    def fail_mat(*_args, **_kwargs):
        raise OSError("mat writer error")

    monkeypatch.setattr(worker_module, "savemat", fail_mat)
    worker = PowerMonitorExportWorker(_payload(), {"CSV": target, "MAT": mat_path}, frozenset({target}))
    results = []
    worker.outcome.connect(results.append)
    worker.run()
    assert results[0].saved == (target,)
    assert len(results[0].errors) == 1 and "mat writer error" in results[0].errors[0]
    assert target.read_bytes() != b"previous"
    assert not mat_path.exists()
    assert set(tmp_path.iterdir()) == {target}


def test_power_monitor_export_is_nonblocking_and_widget_close_defers(qtbot, monkeypatch, tmp_path):
    import logic.power_monitor_export_worker as worker_module

    widget = _widget(qtbot, monkeypatch, tmp_path)
    widget.show()
    started, release = threading.Event(), threading.Event()
    write = worker_module._write_staged_power_monitor_format

    def gated(*args, **kwargs):
        started.set()
        if not release.wait(4):
            raise TimeoutError("gate expired")
        return write(*args, **kwargs)

    monkeypatch.setattr(worker_module, "_write_staged_power_monitor_format", gated)
    try:
        widget.save_recording()
        qtbot.waitUntil(started.is_set, timeout=3000)
        assert widget.power_monitor_export_busy
        assert widget._power_monitor_export_task.worker_thread.parent() is widget
        assert not widget.save_recording_button.isEnabled()
        processed = []
        QTimer.singleShot(0, lambda: processed.append(True))
        qtbot.waitUntil(lambda: bool(processed), timeout=500)
        widget.save_recording()
        assert len(widget._owned_power_monitor_export_tasks) == 1
        widget.close()
        assert widget.isVisible()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not widget.power_monitor_export_busy and not widget.isVisible(), timeout=5000)
    assert (tmp_path / "saved.csv").is_file()
    assert (tmp_path / "saved.mat").is_file()
    assert not list(tmp_path.glob(".iopanel-monitor-*"))


def test_power_monitor_mainwindow_close_defers_until_native_thread_destroyed(qtbot, monkeypatch, tmp_path):
    import logic.power_monitor_export_worker as worker_module

    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _self: None)
    window = main_window_module.MainWindow(
        AppConfig(instruments={"ct400_backend": "simulation"}),
        settings=AppSettings(QSettings(str(tmp_path / "main.ini"), QSettings.Format.IniFormat)),
    )
    qtbot.addWidget(window)
    window.show()
    recording = _recording()
    window._handle_power_monitor_recording_started(recording.settings)
    window._handle_power_monitor_recording_completed(recording)
    widget = window.power_monitor_trace_widget
    assert widget is not None
    _dialog(monkeypatch, tmp_path, mat=False)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)
    started, release = threading.Event(), threading.Event()
    writer = worker_module._write_staged_power_monitor_format

    def gated(*args, **kwargs):
        started.set()
        if not release.wait(4):
            raise TimeoutError("gate expired")
        return writer(*args, **kwargs)

    monkeypatch.setattr(worker_module, "_write_staged_power_monitor_format", gated)
    try:
        widget.save_recording()
        qtbot.waitUntil(started.is_set, timeout=3000)
        window.close()
        assert window.isVisible()
        assert window._pending_power_monitor_export_close
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert not widget.power_monitor_export_busy
    assert (tmp_path / "saved.csv").is_file()


def test_replaced_recording_remains_selected_after_earlier_export(qtbot, monkeypatch, tmp_path):
    import logic.power_monitor_export_worker as worker_module

    widget = _widget(qtbot, monkeypatch, tmp_path)
    started, release = threading.Event(), threading.Event()
    original = widget.current_recording
    writer = worker_module._write_staged_power_monitor_format

    def gated(*args, **kwargs):
        started.set()
        if not release.wait(4):
            raise TimeoutError("gate expired")
        return writer(*args, **kwargs)

    monkeypatch.setattr(worker_module, "_write_staged_power_monitor_format", gated)
    try:
        widget.save_recording()
        qtbot.waitUntil(started.is_set, timeout=3000)
        widget.start_recording(original.settings)
        assert widget.current_recording is None
        assert not widget.save_recording_button.isEnabled()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not widget.power_monitor_export_busy, timeout=5000)
    assert widget.current_recording is None
    assert not widget.save_recording_button.isEnabled()
    widget.close()
