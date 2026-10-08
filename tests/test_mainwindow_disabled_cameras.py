import io
from datetime import UTC, datetime

import numpy as np
from PySide6.QtCore import QSettings

import app
from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui import main_window as main_window_module


def test_mainwindow_starts_offline_with_all_cameras_disabled(qtbot, monkeypatch, tmp_path):
    config_path = tmp_path / "disabled-cameras.ini"
    config_path.write_text(
        "[App]\nname = Disabled cameras startup test\n\n"
        "[Camera:Disabled camera]\nidentifier = disabled-camera\nenabled = false\nname = Disabled camera\n"
        "backend = vimba\n",
        encoding="utf-8",
    )
    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))

    # Keep startup independent from CT400 hardware and make any camera worker
    # construction or Vimba startup fail immediately in this regression.
    monkeypatch.setattr(main_window_module.MainWindow, "_init_ct400_lazy", lambda _window: None)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_start_vimbasystem",
        lambda _window: (_ for _ in ()).throw(AssertionError("Vimba must not start")),
    )
    monkeypatch.setattr(
        main_window_module,
        "CameraInitWorker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("camera worker must not start")),
    )

    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    assert window.histogram_plot_layout.spacing() == 4
    assert window.histogram_plot_layout.stretch(0) == 1
    window.show()

    open_scan = next(action for action in window.menuBar().actions() if action.text() == "&File")
    open_scan_action = next(action for action in open_scan.menu().actions() if action.objectName() == "openScanAction")
    assert open_scan_action.text() == "Open Scan..."
    before = window.plot_widget.current_measurement
    dialog_calls = []

    def cancel_dialog(*_args, **_kwargs):
        dialog_calls.append(True)
        return "", ""

    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", cancel_dialog)
    open_scan_action.trigger()
    assert len(dialog_calls) == 1
    window.plot_widget.load_btn.click()
    assert len(dialog_calls) == 2
    assert window.plot_widget.current_measurement is before
    assert open_scan_action.isEnabled()
    assert window.plot_widget.load_btn.isEnabled()

    window._handle_ct400_scan_started()
    assert not open_scan_action.isEnabled()
    assert not window.plot_widget.load_btn.isEnabled()
    window._open_scan_file()  # Defensive guard also covers direct slot invocation.
    window.plot_widget.load_btn.click()
    assert len(dialog_calls) == 2
    window._handle_ct400_scan_finished()
    assert open_scan_action.isEnabled()
    assert window.plot_widget.load_btn.isEnabled()

    def start_scan_during_dialog(*_args, **_kwargs):
        dialog_calls.append(True)
        window._handle_ct400_scan_started()
        return str(tmp_path / "not-read.csv"), "IOPanel CSV (*.csv)"

    monkeypatch.setattr(main_window_module.QFileDialog, "getOpenFileName", start_scan_during_dialog)
    monkeypatch.setattr(
        main_window_module,
        "load_scan",
        lambda _path: (_ for _ in ()).throw(AssertionError("scan import must stop after scan starts")),
    )
    window._open_scan_file()
    assert window.plot_widget.current_measurement is before
    assert len(dialog_calls) == 3
    window._handle_ct400_scan_finished()

    qtbot.waitUntil(
        lambda: any(action.text() == "No enabled cameras found in config" for action in window.cameras_menu.actions()),
        timeout=2000,
    )
    placeholder = next(
        action for action in window.cameras_menu.actions() if action.text() == "No enabled cameras found in config"
    )
    assert not placeholder.isEnabled()
    assert window._init_tasks == set()

    window.close()


def test_import_confirms_unsaved_acquisition_and_preserves_reference(qtbot, monkeypatch, tmp_path):
    config_path = tmp_path / "offline-import.ini"
    config_path.write_text("[App]\nname = Offline import test\n", encoding="utf-8")
    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(main_window_module.MainWindow, "_init_ct400_lazy", lambda _window: None)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_start_vimbasystem",
        lambda _window: (_ for _ in ()).throw(AssertionError("Vimba must not start")),
    )
    monkeypatch.setattr(
        main_window_module,
        "CameraInitWorker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("camera worker must not start")),
    )

    window = main_window_module.MainWindow(config, settings=settings)
    qtbot.addWidget(window)
    detectors = (Detector.DE_1,)
    acquisition_settings = ScanAcquisitionSettings(
        1500.0, 1501.0, 20, "5", "2.5", "mW", 2.5, LaserInput.LI_2, detectors
    )
    acquired = ScanMeasurement(
        acquisition_settings,
        np.array([1500.0, 1501.0]),
        np.array([[-10.0, -11.0]]),
        detectors,
        None,
        CT400ScanResultKind.SUCCESS,
        0,
        "",
        "dummy",
        True,
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    payload = build_scan_export_v2(acquired, comment="offline fixture")
    text_buffer = io.StringIO()
    np.savetxt(text_buffer, payload.csv_data, delimiter=",", header=payload.csv_header, comments="", fmt="%.6f")
    scan_directory = tmp_path / "scans"
    scan_directory.mkdir()
    scan_path = scan_directory / "saved.csv"
    scan_path.write_text(text_buffer.getvalue(), encoding="utf-8")
    corrupt_path = scan_directory / "corrupt.csv"
    corrupt_path.write_text("not an IOPanel scan", encoding="utf-8")
    selected_path = [str(scan_path)]
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (selected_path[0], "IOPanel CSV (*.csv)"),
    )
    answers = [main_window_module.QMessageBox.StandardButton.No, main_window_module.QMessageBox.StandardButton.Yes]
    prompts = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args: prompts.append(args[2]) or answers.pop(0),
    )
    warnings = []
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    window.plot_widget.set_measurement(acquired)
    window.plot_widget.freeze_current_trace()
    frozen = window.plot_widget.reference_measurement
    assert window.plot_widget.has_unsaved_acquisition()
    window._open_scan_file()
    assert window.plot_widget.current_measurement is acquired
    assert window.plot_widget.reference_measurement is frozen
    assert settings._settings.value("Paths/scan_import") is None

    window._open_scan_file()
    imported = window.plot_widget.current_imported_scan
    assert imported is not None and imported.source_path == scan_path.resolve()
    assert window.plot_widget.reference_measurement is frozen
    assert not window.plot_widget.has_unsaved_acquisition()
    assert not window.plot_widget.save_btn.isEnabled()
    assert settings.directory("Paths/scan_import") == scan_directory.resolve()
    assert len(prompts) == 2

    # Imported data is already on disk, so replacing it does not prompt again.
    window.plot_widget.load_btn.click()
    assert len(prompts) == 2
    imported = window.plot_widget.current_imported_scan
    selected_path[0] = str(corrupt_path)
    window._open_scan_file()
    assert window.plot_widget.current_imported_scan is imported
    assert window.plot_widget.reference_measurement is frozen
    assert settings.directory("Paths/scan_import") == scan_directory.resolve()
    assert warnings

    window.close()
