"""Characterize *actual* MainWindow scan imports and PlotWidget scan exports.

Synthetic, hardware-free Qt offscreen/native-desktop diagnostic. File dialogs,
message boxes and instrument initialization are replaced only within this
standalone diagnostic; production UI paths execute unchanged. Timing excludes
fixture creation and window construction.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6 import QtCore, QtWidgets
from scipy.io import savemat

from app_settings import AppSettings
from config_model import AppConfig
from logic.scan_export import build_scan_export_v2
from logic.scan_import import load_scan
from tools.benchmark_scan_io import _synthetic_scan, _timed_on_qt_thread
from ui import main_window as main_window_module
from ui import plot_widgets
from ui.scan_export_dialog import ScanExportRequest


class _AcceptedExportDialog:
    DialogCode = QtWidgets.QDialog.DialogCode

    def __init__(self, request: ScanExportRequest) -> None:
        self.export_request = request

    def exec(self) -> QtWidgets.QDialog.DialogCode:
        return self.DialogCode.Accepted


def _unexpected_prompt(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Unexpected user confirmation or error during synthetic GUI benchmark")


def characterize(
    points: list[int], detectors: list[int], output: Path
) -> list[dict[str, object]]:
    """Exercise real GUI handlers with deterministic in-memory acquisition data."""
    if not points or any(value < 2 for value in points):
        raise ValueError("All point counts must be >= 2")
    if not detectors or any(value not in (1, 4) for value in detectors):
        raise ValueError("Active optical detector count must be 1 or 4")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    rows: list[dict[str, object]] = []
    config = AppConfig(instruments={"ct400_backend": "simulation"})

    with (
        tempfile.TemporaryDirectory(prefix="iopanel-mainwindow-io-") as temp,
        patch.object(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None),
        patch.object(main_window_module.MainWindow, "_init_ct400_lazy", _unexpected_prompt),
        patch.object(main_window_module.MainWindow, "_start_vimbasystem", _unexpected_prompt),
        patch.object(main_window_module.QMessageBox, "warning", _unexpected_prompt),
        patch.object(main_window_module.QMessageBox, "question", _unexpected_prompt),
        patch.object(plot_widgets.QMessageBox, "information", lambda *_args, **_kwargs: None),
    ):
        folder = Path(temp)
        for count in points:
            for num_detectors in detectors:
                measurement = _synthetic_scan(count, tuple(
                    plot_widgets.Detector(index + 1) for index in range(num_detectors)
                ))
                payload = build_scan_export_v2(measurement, comment="synthetic benchmark")
                csv_path = folder / f"fixture-{count}-{num_detectors}.csv"
                mat_path = folder / f"fixture-{count}-{num_detectors}.mat"
                with io.StringIO() as buffer:
                    np.savetxt(
                        buffer, payload.csv_data, delimiter=",",
                        header=payload.csv_header, comments="", fmt="%.6f",
                    )
                    csv_path.write_text(buffer.getvalue(), encoding="utf-8")
                savemat(mat_path, payload.mat_data, do_compression=True)

                qsettings = QtCore.QSettings(
                    str(folder / f"gui-settings-{count}-{num_detectors}.ini"),
                    QtCore.QSettings.Format.IniFormat,
                )
                settings = AppSettings(qsettings)
                window = main_window_module.MainWindow(config, settings=settings)
                window.show()
                app.processEvents()

                def import_foreground(path: Path) -> None:
                    with patch.object(
                        main_window_module.QFileDialog, "getOpenFileName",
                        return_value=(str(path), "IOPanel scan"),
                    ):
                        window._open_scan_file()
                    imported = window.plot_widget.current_imported_scan
                    if imported is None or imported.source_path != path.resolve():
                        raise AssertionError("MainWindow foreground scan import did not complete")

                def import_overlay(path: Path) -> None:
                    before = len(window.plot_widget.scan_overlays)
                    with patch.object(
                        main_window_module.QFileDialog, "getOpenFileName",
                        return_value=(str(path), "IOPanel scan"),
                    ):
                        window._add_scan_to_plot()
                    if len(window.plot_widget.scan_overlays) != before + 1:
                        raise AssertionError("MainWindow scan overlay import did not complete")

                try:
                    tasks = (
                        ("import_csv_foreground", csv_path, lambda: import_foreground(csv_path)),
                        ("import_mat_overlay", mat_path, lambda: import_overlay(mat_path)),
                    )
                    for name, path, handler in tasks:
                        _, timing = _timed_on_qt_thread(app, handler)
                        rows.append({
                            "points_per_scan": count,
                            "detector_count": num_detectors,
                            "operation": name,
                            "qt_platform": app.platformName(),
                            "window_system": sys.platform,
                            "file_bytes": path.stat().st_size,
                            **timing,
                        })

                    for kind in ("csv", "mat"):
                        # Preparing the active plot is excluded from export-handler timing.
                        window.plot_widget.set_measurement(measurement)
                        export_request = ScanExportRequest(
                            folder, f"gui-export-{count}-{num_detectors}-{kind}",
                            kind == "csv", kind == "mat", False, False, "benchmark note",
                        )
                        with patch.object(
                            plot_widgets, "ScanExportDialog",
                            side_effect=lambda *_args, **_kwargs: _AcceptedExportDialog(export_request),
                        ):
                            _, timing = _timed_on_qt_thread(app, window.plot_widget.save_scan_data)
                        target = folder / f"{export_request.base_name}.{kind}"
                        if not target.is_file() or not target.stat().st_size:
                            raise AssertionError("PlotWidget export handler failed to write scan")
                        exported = load_scan(target)
                        if exported.detectors != measurement.detectors or exported.comment != export_request.comment:
                            raise AssertionError("Export handler changed schema-v2 detector/comment metadata")
                        if not np.allclose(exported.detector_data, measurement.detector_data, atol=1e-6, rtol=0):
                            raise AssertionError("Export handler changed detector samples")
                        rows.append({
                            "points_per_scan": count,
                            "detector_count": num_detectors,
                            "operation": f"export_{kind}",
                            "qt_platform": app.platformName(),
                            "window_system": sys.platform,
                            "file_bytes": target.stat().st_size,
                            **timing,
                        })
                finally:
                    window.close()
                    app.processEvents()

    output.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, nargs="+", default=[10_000, 50_000])
    parser.add_argument("--detectors", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = characterize(args.points, args.detectors, args.output)
    print(f"Measured {len(result)} real GUI handler cases with simulated fixtures")


if __name__ == "__main__":
    main()
