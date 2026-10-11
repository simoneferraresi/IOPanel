"""Driver-free asynchronous scan-import GUI lifecycle regressions (Issue #155)."""

from __future__ import annotations

import io
import threading
from datetime import UTC, datetime

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from scipy.io import savemat

from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_import import ScanImportError
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui import main_window as main_window_module


def _window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _self: None)
    window = main_window_module.MainWindow(
        AppConfig(instruments={"ct400_backend": "simulation"}),
        settings=AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)),
    )
    qtbot.addWidget(window)
    window.show()
    return window


def _scan_file(tmp_path, kind="csv"):
    detectors = (Detector.DE_2, Detector.DE_1)
    settings = ScanAcquisitionSettings(1510.0, 1510.02, 10, "5", "1", "mW", 1.0, LaserInput.LI_1, detectors)
    measurement = ScanMeasurement(
        settings,
        np.array([1510.0, 1510.01, 1510.02]),
        np.array([[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]]),
        detectors,
        None,
        CT400ScanResultKind.SUCCESS,
        0,
        "",
        "synthetic",
        True,
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    export = build_scan_export_v2(measurement, comment="async import test")
    path = tmp_path / f"saved.{kind}"
    if kind == "csv":
        buffer = io.StringIO()
        np.savetxt(buffer, export.csv_data, delimiter=",", header=export.csv_header, comments="", fmt="%.6f")
        path.write_text(buffer.getvalue(), encoding="utf-8")
    else:
        savemat(path, export.mat_data, do_compression=True)
    return path, measurement


def _select(monkeypatch, path):
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(path), "IOPanel Scan"),
    )


@pytest.mark.parametrize("kind", ["csv", "mat"])
@pytest.mark.parametrize("overlay", [False, True])
def test_async_import_real_schema_roundtrip_offline(qtbot, monkeypatch, tmp_path, kind, overlay):
    window = _window(qtbot, monkeypatch, tmp_path)
    path, original = _scan_file(tmp_path, kind)
    _select(monkeypatch, path)
    if overlay:
        window._add_scan_to_plot()
    else:
        window._open_scan_file()
    assert window._scan_import_task is not None
    assert not window.open_scan_action.isEnabled()
    assert not window.add_scan_action.isEnabled()
    qtbot.waitUntil(lambda: window._scan_import_task is None, timeout=5000)
    assert window.open_scan_action.isEnabled()
    if overlay:
        assert len(window.plot_widget.scan_overlays) == 1
        imported = window.plot_widget.scan_overlays[0].scan
    else:
        imported = window.plot_widget.current_imported_scan
        assert imported is not None
        assert not window.plot_widget.save_btn.isEnabled()
    assert imported.detectors == original.detectors
    assert imported.comment == "async import test"
    assert not imported.wavelengths_nm.flags.writeable
    assert not imported.detector_data.flags.writeable
    np.testing.assert_array_equal(imported.detector_data, original.detector_data)
    window.close()


def test_in_flight_scan_import_is_nonblocking_and_rejects_duplicate(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    path, _ = _scan_file(tmp_path)
    _select(monkeypatch, path)
    started, release = threading.Event(), threading.Event()
    real_loader = main_window_module.load_scan
    calls = []

    def blocked_loader(target):
        calls.append(str(target))
        started.set()
        if not release.wait(4):
            raise TimeoutError("test gate never released")
        return real_loader(target)

    monkeypatch.setattr(main_window_module, "load_scan", blocked_loader)
    try:
        window._open_scan_file()
        qtbot.waitUntil(started.is_set, timeout=3000)
        assert window._scan_import_task is not None
        assert window._scan_import_task.worker_thread.parent() is window
        # No worker result required for the main Qt event loop to process a timer.
        events = []
        main_window_module.QTimer.singleShot(0, lambda: events.append("responsive"))
        qtbot.waitUntil(lambda: bool(events), timeout=500)
        window._add_scan_to_plot()
        assert len(calls) == 1
        assert window.plot_widget.current_imported_scan is None
    finally:
        release.set()
    qtbot.waitUntil(lambda: window._scan_import_task is None, timeout=5000)
    qtbot.waitUntil(lambda: not window._owned_scan_import_tasks, timeout=5000)
    assert window.plot_widget.current_imported_scan is not None
    window.close()


def test_scan_started_during_parsing_discarded_even_after_scan_finishes(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    path, _ = _scan_file(tmp_path)
    _select(monkeypatch, path)
    started, release = threading.Event(), threading.Event()
    loader = main_window_module.load_scan

    def gated(target):
        started.set()
        if not release.wait(4):
            raise TimeoutError("test gate never released")
        return loader(target)

    monkeypatch.setattr(main_window_module, "load_scan", gated)
    try:
        window._open_scan_file()
        qtbot.waitUntil(started.is_set, timeout=3000)
        window._handle_ct400_scan_started()
        window._handle_ct400_scan_finished()
    finally:
        release.set()
    qtbot.waitUntil(lambda: window._scan_import_task is None, timeout=5000)
    assert window.plot_widget.current_imported_scan is None
    assert window.open_scan_action.isEnabled()
    window.close()


def test_scan_import_close_waits_without_blocking_and_ignores_late_result(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    path, _ = _scan_file(tmp_path)
    _select(monkeypatch, path)
    started, release = threading.Event(), threading.Event()
    loader = main_window_module.load_scan

    def gated(target):
        started.set()
        if not release.wait(4):
            raise TimeoutError("test gate never released")
        return loader(target)

    monkeypatch.setattr(main_window_module, "load_scan", gated)
    try:
        window._open_scan_file()
        qtbot.waitUntil(started.is_set, timeout=3000)
        window.close()
        assert window._pending_scan_import_close
        assert window.isVisible()
        assert window.plot_widget.current_imported_scan is None
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert window._scan_import_task is None
    assert window.plot_widget.current_imported_scan is None


def test_import_errors_report_on_ui_thread_without_mutating_plot(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    path, _ = _scan_file(tmp_path)
    _select(monkeypatch, path)
    warnings = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args[-1]),
    )

    def reject(_path):
        raise ScanImportError("injected malformed data")

    monkeypatch.setattr(main_window_module, "load_scan", reject)
    window._open_scan_file()
    qtbot.waitUntil(lambda: window._scan_import_task is None, timeout=5000)
    assert len(warnings) == 1 and "injected malformed" in warnings[0]
    assert window.plot_widget.current_imported_scan is None
    assert window.open_scan_action.isEnabled()
    window.close()
