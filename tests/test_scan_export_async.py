"""Offline CSV/MAT export worker, overwrite and GUI shutdown regressions."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QDialog

from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400_types import Detector
from logic.scan_export import build_scan_export_v2
from logic.scan_export_worker import ScanExportWorker, _write_staged_format
from logic.scan_import import load_scan
from tools.benchmark_scan_io import _synthetic_scan
from ui import main_window as main_window_module
from ui import plot_widgets
from ui.control_panel import ScanSettings
from ui.scan_export_dialog import ScanExportRequest


def _payload():
    return build_scan_export_v2(
        _synthetic_scan(20, (Detector.DE_3, Detector.DE_1)),
        comment="row one\nrow two",
    )


def _dialog(monkeypatch, directory: Path, formats=(True, True, False), name="scan"):
    class Dialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *_args):
            self.export_request = ScanExportRequest(directory, name, *formats, False, "row one\nrow two")

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(plot_widgets, "ScanExportDialog", Dialog)


def _widget(qtbot, monkeypatch, directory: Path):
    widget = plot_widgets.PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_synthetic_scan(20, (Detector.DE_3, Detector.DE_1)))
    _dialog(monkeypatch, directory)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_a: None)
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *_a: None)
    return widget


def test_staged_worker_preserves_schema_and_no_temp_files(tmp_path):
    payload = _payload()
    destinations = {"CSV": tmp_path / "scan.csv", "MAT": tmp_path / "scan.mat"}
    worker = ScanExportWorker(payload, destinations, frozenset())
    outcomes = []
    worker.outcome.connect(outcomes.append)

    worker.run()

    assert len(outcomes) == 1
    assert outcomes[0].errors == ()
    assert set(outcomes[0].saved) == set(destinations.values())
    for path in destinations.values():
        scan = load_scan(path)
        assert scan.detectors == (Detector.DE_3, Detector.DE_1)
        assert scan.comment == "row one\nrow two"
        np.testing.assert_allclose(
            scan.detector_data, _synthetic_scan(20, (Detector.DE_3, Detector.DE_1)).detector_data, atol=1e-6
        )
    assert set(tmp_path.iterdir()) == set(destinations.values())


def test_staging_failure_retains_previous_files_and_cleans_up(monkeypatch, tmp_path):
    import logic.scan_export_worker as export_worker

    existing = tmp_path / "scan.csv"
    existing.write_bytes(b"original-content")

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(export_worker.np, "savetxt", fail)
    with pytest.raises(OSError, match="disk full"):
        _write_staged_format("CSV", existing, _payload(), overwrite_confirmed=True)
    assert existing.read_bytes() == b"original-content"
    assert list(tmp_path.iterdir()) == [existing]


def test_new_target_appearing_during_save_is_never_overwritten(monkeypatch, tmp_path):
    import logic.scan_export_worker as export_worker

    target = tmp_path / "scan.csv"
    original = export_worker.np.savetxt

    def racing_save(*args, **kwargs):
        target.write_bytes(b"racer-created")
        return original(*args, **kwargs)

    monkeypatch.setattr(export_worker.np, "savetxt", racing_save)
    with pytest.raises(FileExistsError):
        _write_staged_format("CSV", target, _payload(), overwrite_confirmed=False)
    assert target.read_bytes() == b"racer-created"
    assert list(tmp_path.iterdir()) == [target]


def test_confirmed_overwrite_is_atomic_and_format_failures_independent(monkeypatch, tmp_path):
    import logic.scan_export_worker as export_worker

    csv_path = tmp_path / "scan.csv"
    mat_path = tmp_path / "scan.mat"
    csv_path.write_bytes(b"before")

    def fail_mat(*_args, **_kwargs):
        raise OSError("MAT writer failed")

    monkeypatch.setattr(export_worker.sio, "savemat", fail_mat)
    worker = ScanExportWorker(_payload(), {"CSV": csv_path, "MAT": mat_path}, frozenset({csv_path}))
    outcomes = []
    worker.outcome.connect(outcomes.append)
    worker.run()

    assert outcomes[0].saved == (csv_path,)
    assert len(outcomes[0].errors) == 1 and "MAT writer failed" in outcomes[0].errors[0]
    assert csv_path.read_bytes() != b"before"
    assert not mat_path.exists()
    assert set(tmp_path.iterdir()) == {csv_path}


def test_export_is_nonblocking_and_direct_widget_close_defers(qtbot, monkeypatch, tmp_path):
    import logic.scan_export_worker as export_worker

    widget = _widget(qtbot, monkeypatch, tmp_path)
    widget.show()
    started = threading.Event()
    release = threading.Event()
    real_writer = export_worker._write_staged_format

    def blocked_writer(*args, **kwargs):
        started.set()
        if not release.wait(4):
            raise TimeoutError("Test gate not released")
        return real_writer(*args, **kwargs)

    monkeypatch.setattr(export_worker, "_write_staged_format", blocked_writer)
    try:
        widget.save_scan_data()
        qtbot.waitUntil(started.is_set, timeout=3000)
        assert widget.scan_export_busy
        assert widget.pending_saves == 1
        assert not widget.save_btn.isEnabled()

        processed = []
        QTimer.singleShot(0, lambda: processed.append(True))
        qtbot.waitUntil(lambda: bool(processed), timeout=500)
        widget.save_scan_data()
        assert widget.pending_saves == 1

        widget.close()
        assert widget.isVisible()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not widget.scan_export_busy and not widget.isVisible(), timeout=5000)
    assert (tmp_path / "scan.csv").exists()
    assert (tmp_path / "scan.mat").exists()
    assert not list(tmp_path.glob(".iopanel-scan-*"))


def test_mainwindow_close_waits_for_file_worker(qtbot, monkeypatch, tmp_path):
    import logic.scan_export_worker as export_worker

    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _self: None)
    window = main_window_module.MainWindow(
        AppConfig(instruments={"ct400_backend": "simulation"}),
        settings=AppSettings(QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)),
    )
    qtbot.addWidget(window)
    window.show()
    window.plot_widget.set_measurement(_synthetic_scan(20, (Detector.DE_1,)))
    _dialog(monkeypatch, tmp_path, (True, False, False))
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_a: None)
    started, release = threading.Event(), threading.Event()
    writer = export_worker._write_staged_format

    def blocked_writer(*args, **kwargs):
        started.set()
        if not release.wait(4):
            raise TimeoutError("Test gate not released")
        return writer(*args, **kwargs)

    monkeypatch.setattr(export_worker, "_write_staged_format", blocked_writer)
    try:
        window.plot_widget.save_scan_data()
        qtbot.waitUntil(started.is_set, timeout=3000)
        window.close()
        assert window.isVisible()
        assert window._pending_scan_export_close
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert not window.plot_widget.scan_export_busy
    assert (tmp_path / "scan.csv").is_file()


def test_new_measurement_remains_unsaved_after_prior_export_finishes(qtbot, monkeypatch, tmp_path):
    import logic.scan_export_worker as export_worker

    widget = _widget(qtbot, monkeypatch, tmp_path)
    started, release = threading.Event(), threading.Event()
    original_writer = export_worker._write_staged_format
    original = widget.current_measurement

    def writer(*args, **kwargs):
        started.set()
        if not release.wait(4):
            raise TimeoutError("Test gate not released")
        return original_writer(*args, **kwargs)

    monkeypatch.setattr(export_worker, "_write_staged_format", writer)
    try:
        widget.save_scan_data()
        qtbot.waitUntil(started.is_set, timeout=3000)
        replacement = _synthetic_scan(20, (Detector.DE_2,))
        widget.set_measurement(replacement)
        assert widget.has_unsaved_acquisition()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not widget.scan_export_busy, timeout=5000)
    assert widget.current_measurement is replacement
    assert widget._saved_measurement_ref() is original
    assert widget.has_unsaved_acquisition()
    widget.close()
