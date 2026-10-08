"""Profile schema-v2 imports and plot responsiveness with deterministic synthetic scans.

Run on a desktop Windows session for representative GUI timing, or with
QT_QPA_PLATFORM=offscreen for operation-level comparisons. This tool never
constructs or connects laboratory hardware.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from PySide6.QtTest import QTest
from scipy.io import savemat

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_import import MAX_SCAN_FILE_BYTES, ImportedScan, load_scan
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui.control_panel import ScanSettings
from ui.plot_widgets import MAX_OVERLAY_SCANS, PlotWidget
from ui.typography import install_application_fonts


def _synthetic_measurement(points: int, detectors: tuple[Detector, ...], phase: float) -> ScanMeasurement:
    wavelengths = np.linspace(1500.0, 1600.0, points, dtype=np.float64)
    center = 1548.271 + phase * 0.13
    resonance = -12.0 * np.exp(-(((wavelengths - center) / 0.006) ** 2))
    rows = np.vstack(
        [
            -22.0 + row * 1.5 + 1.4 * np.sin(wavelengths * 0.19 + phase * 0.7 + row) + resonance * (1.0 - 0.08 * row)
            for row in range(len(detectors))
        ]
    )
    for row in range(len(detectors)):
        rows[row, (row + 101) :: 20_003] = np.nan
        rows[row, (row + 509) :: 37_001] = np.inf
    settings = ScanAcquisitionSettings(
        1500.0,
        1600.0,
        1,
        "1",
        "5",
        "mW",
        5.0,
        LaserInput.LI_2,
        detectors,
    )
    return ScanMeasurement(
        settings,
        wavelengths,
        rows,
        detectors,
        None,
        CT400ScanResultKind.SUCCESS,
        0,
        "synthetic performance fixture",
        "hardware.dummy_ct400.DummyCT400",
        True,
        datetime.now(UTC),
    )


def _write_fixtures(
    root: Path, points: int, detectors: tuple[Detector, ...]
) -> tuple[list[ImportedScan], dict[str, float]]:
    measurement = _synthetic_measurement(points, detectors, 0.0)
    payload = build_scan_export_v2(measurement, comment="deterministic overlay performance profile")
    paths: list[Path] = []
    parse_times: dict[str, float] = {}
    for index in range(MAX_OVERLAY_SCANS + 1):
        stem = root / f"scan-{points}-{len(detectors)}det-{index}"
        csv_path = stem.with_suffix(".csv")
        np.savetxt(csv_path, payload.csv_data, delimiter=",", header=payload.csv_header, comments="", fmt="%.9g")
        mat_path = stem.with_suffix(".mat")
        savemat(mat_path, payload.mat_data, do_compression=True)
        if csv_path.stat().st_size > MAX_SCAN_FILE_BYTES or mat_path.stat().st_size > MAX_SCAN_FILE_BYTES:
            raise ValueError(f"Generated fixture exceeds the scan importer size limit: {stem}")
        paths.extend((csv_path, mat_path))

    scans: list[ImportedScan] = []
    for index, path in enumerate(paths[::2]):
        start = time.perf_counter()
        scan = load_scan(path)
        parse_times[f"csv_{index}"] = time.perf_counter() - start
        scans.append(scan)
    start = time.perf_counter()
    mat_scan = load_scan(paths[1])
    parse_times["mat"] = time.perf_counter() - start
    # Reuse immutable arrays in the plot workload while retaining a distinct source identity.
    scans[1] = mat_scan
    parse_times["csv_avg"] = float(np.mean([parse_times[f"csv_{index}"] for index in range(len(scans))]))
    return scans, parse_times


def _timed_class_method(target: type, method_name: str, totals: dict[str, float], label: str) -> None:
    original = getattr(target, method_name)

    def measured(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            totals[label] += time.perf_counter() - start

    setattr(target, method_name, measured)


def _profile_plot(
    scans: list[ImportedScan],
    overlay_count: int,
    *,
    crosshair: bool,
    dash: bool,
    downsampling: bool = True,
    legend: bool = True,
    auto_downsample_factor: float = 1.0,
    mouse_events: bool = False,
) -> dict[str, float]:
    timings: defaultdict[str, float] = defaultdict(float)
    saved_methods = {
        (pg.PlotDataItem, "setData"): pg.PlotDataItem.setData,
        (pg.LegendItem, "addItem"): pg.LegendItem.addItem,
        (pg.ViewBox, "autoRange"): pg.ViewBox.autoRange,
        (pg.TextItem, "setText"): pg.TextItem.setText,
        (pg.PlotDataItem, "setClipToView"): pg.PlotDataItem.setClipToView,
        (pg.PlotDataItem, "setDownsampling"): pg.PlotDataItem.setDownsampling,
        (pg.PlotItem, "plot"): pg.PlotItem.plot,
        (pg.LegendItem, "clear"): pg.LegendItem.clear,
        (PlotWidget, "_refresh_scan_legend"): PlotWidget._refresh_scan_legend,
        (PlotWidget, "add_imported_overlay"): PlotWidget.add_imported_overlay,
    }
    try:
        for (target, method), label in (
            ((pg.PlotDataItem, "setData"), "setData_s"),
            ((pg.LegendItem, "addItem"), "legend_add_s"),
            ((pg.ViewBox, "autoRange"), "autoRange_s"),
            ((pg.TextItem, "setText"), "text_set_s"),
            ((pg.PlotDataItem, "setClipToView"), "clip_config_s"),
            ((pg.PlotDataItem, "setDownsampling"), "downsample_config_s"),
            ((pg.PlotItem, "plot"), "plot_item_construct_s"),
            ((pg.LegendItem, "clear"), "legend_clear_s"),
            ((PlotWidget, "_refresh_scan_legend"), "legend_refresh_s"),
            ((PlotWidget, "add_imported_overlay"), "overlay_method_s"),
        ):
            _timed_class_method(target, method, timings, label)
        widget = PlotWidget(ScanSettings())
        widget.resize(1200, 760)
        widget.set_imported_scan(scans[0])
        widget.show()
        QtWidgets.QApplication.processEvents()
        add_start = time.perf_counter()
        if not dash:
            for scan in scans[1 : overlay_count + 1]:
                original_scan = scan
                original_method = widget.add_imported_overlay

                def add_solid(scan_to_add=original_scan, add_method=original_method):
                    original_plot = widget.plot_widget.plot

                    def solid_plot(*args, **kwargs):
                        pen = kwargs.get("pen")
                        if pen is not None:
                            kwargs["pen"] = pg.mkPen(
                                pen.color(), width=pen.widthF(), style=QtCore.Qt.PenStyle.SolidLine
                            )
                        return original_plot(*args, **kwargs)

                    widget.plot_widget.plot = solid_plot
                    try:
                        return add_method(scan_to_add)
                    finally:
                        widget.plot_widget.plot = original_plot

                add_solid()
        else:
            for scan in scans[1 : overlay_count + 1]:
                widget.add_imported_overlay(scan)
        timings["overlay_setup_including_legend_range_s"] = time.perf_counter() - add_start
        visible = [item for entry in widget.scan_overlays for item in entry.items.values()]
        visible.extend(widget.detector_plot_items.values())
        for item in visible:
            item.setClipToView(downsampling)
            item.opts["autoDownsampleFactor"] = auto_downsample_factor
            item.setDownsampling(ds=1, auto=False, method="peak")
            item.setDownsampling(ds=1, auto=downsampling, method="peak")
        if not legend and widget.detector_legend is not None:
            widget.detector_legend.setVisible(False)
        timings["plotted_item_count"] = float(len(visible))
        timings["displayed_curve_point_count"] = float(
            sum(len(item.curve.xData) for item in visible if item.curve.xData is not None)
        )
        current = scans[0]
        mouse_pos = widget.plot_widget.plotItem.vb.mapViewToScene(
            QtCore.QPointF(float(current.wavelengths_nm[len(current.wavelengths_nm) // 2]), -20.0)
        )
        crosshair_samples = []
        for _ in range(60):
            start = time.perf_counter()
            widget._on_mouse_moved(mouse_pos if crosshair else QtCore.QPointF(-100.0, -100.0))
            crosshair_samples.append(time.perf_counter() - start)
        timings["mouse_inside_p50_ms"] = float(np.percentile(crosshair_samples, 50) * 1000)
        timings["mouse_inside_p95_ms"] = float(np.percentile(crosshair_samples, 95) * 1000)
        timings["text_set_total_s"] = timings.pop("text_set_s", 0.0)
        start = time.perf_counter()
        widget.grab()
        timings["grab_repaint_s"] = time.perf_counter() - start
        pan_zoom = []
        for index in range(10):
            start = time.perf_counter()
            widget.plot_widget.plotItem.vb.setRange(xRange=[1540 + index * 0.2, 1550 + index * 0.2])
            QtWidgets.QApplication.processEvents()
            pan_zoom.append(time.perf_counter() - start)
        timings["pan_zoom_p50_ms"] = float(np.percentile(pan_zoom, 50) * 1000)
        event_lags: list[float] = []
        for _ in range(20):
            start = time.perf_counter()
            QtCore.QTimer.singleShot(0, lambda began=start: event_lags.append(time.perf_counter() - began))
            QtWidgets.QApplication.processEvents()
        timings["event_loop_p50_ms"] = float(np.percentile(event_lags, 50) * 1000) if event_lags else -1.0
        if mouse_events:
            processed_positions: list[QtCore.QPointF] = []
            original_handler = widget._on_mouse_moved
            if not crosshair:
                widget.plot_widget.scene().sigMouseMoved.disconnect(widget._queue_mouse_moved)

            def capture_handler(position):
                processed_positions.append(QtCore.QPointF(position))
                return original_handler(position)

            widget._on_mouse_moved = capture_handler
            viewport = widget.plot_widget.viewport()
            start = time.perf_counter()
            for x_pos in range(120, 620, 5):
                QTest.mouseMove(viewport, QtCore.QPoint(x_pos, 250), delay=0)
                QtWidgets.QApplication.processEvents()
            QTest.qWait(160)
            timings["mouse_burst_elapsed_ms"] = (time.perf_counter() - start) * 1000
            timings["mouse_burst_processed_events"] = float(len(processed_positions))
        if widget.scan_overlays:
            start = time.perf_counter()
            widget.set_overlay_visible(widget.scan_overlays[-1].identity, False)
            timings["hide_refresh_s"] = time.perf_counter() - start
            start = time.perf_counter()
            widget.set_overlay_visible(widget.scan_overlays[-1].identity, True)
            timings["show_refresh_s"] = time.perf_counter() - start
        widget.close()
        QtWidgets.QApplication.processEvents()
    finally:
        for (target, method), original in saved_methods.items():
            setattr(target, method, original)
    return dict(timings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    parser.add_argument("--detectors", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--solid", action="store_true", help="Use solid rather than normal scan line styles")
    parser.add_argument("--no-downsampling", action="store_true")
    parser.add_argument("--no-legend", action="store_true")
    parser.add_argument("--no-crosshair", action="store_true")
    parser.add_argument("--auto-downsample-factor", type=float, default=1.0)
    parser.add_argument("--mouse-events", action="store_true")
    args = parser.parse_args()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    install_application_fonts(app)
    report: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="iopanel-overlay-profile-") as temp:
        root = Path(temp)
        for points in args.points:
            for detector_count in args.detectors:
                detectors = tuple(Detector(index + 1) for index in range(detector_count))
                scans, parsing = _write_fixtures(root, points, detectors)
                for overlay_count in (0, 1, 2, 4, 8):
                    report.append(
                        {
                            "points_per_scan": points,
                            "detector_count": detector_count,
                            "overlay_count": overlay_count,
                            "csv_parse_s": parsing["csv_avg"],
                            "mat_parse_s": parsing["mat"],
                            **_profile_plot(
                                scans,
                                overlay_count,
                                crosshair=not args.no_crosshair,
                                dash=not args.solid,
                                downsampling=not args.no_downsampling,
                                legend=not args.no_legend,
                                auto_downsample_factor=args.auto_downsample_factor,
                                mouse_events=args.mouse_events,
                            ),
                        }
                    )
    encoded = json.dumps(report, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
