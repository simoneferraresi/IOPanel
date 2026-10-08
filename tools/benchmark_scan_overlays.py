"""Profile schema-v2 imports and plot responsiveness with deterministic synthetic scans.

Run on a desktop Windows session for representative GUI timing, or with
QT_QPA_PLATFORM=offscreen for operation-level comparisons. This tool never
constructs or connects laboratory hardware.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
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

_LEGACY_OVERLAY_LINE_STYLES = (
    QtCore.Qt.PenStyle.DashLine,
    QtCore.Qt.PenStyle.DotLine,
    QtCore.Qt.PenStyle.DashDotLine,
    QtCore.Qt.PenStyle.DashDotDotLine,
)


def _synthetic_measurement(
    points: int,
    detectors: tuple[Detector, ...],
    phase: float,
    nonfinite_pattern: str,
) -> ScanMeasurement:
    start_nm = 1500.0 + phase * 0.17
    stop_nm = 1600.0 - phase * 0.11
    wavelengths = np.linspace(start_nm, stop_nm, points, dtype=np.float64)
    center = 1548.271 + phase * 0.13
    resonance = -12.0 * np.exp(-(((wavelengths - center) / 0.006) ** 2))
    rows = np.vstack(
        [
            -22.0 + row * 1.5 + 1.4 * np.sin(wavelengths * 0.19 + phase * 0.7 + row) + resonance * (1.0 - 0.08 * row)
            for row in range(len(detectors))
        ]
    )
    if nonfinite_pattern in {"nan", "mixed"}:
        for row in range(len(detectors)):
            rows[row, (row + 101) :: 20_003] = np.nan
    if nonfinite_pattern in {"inf", "mixed"}:
        for row in range(len(detectors)):
            rows[row, (row + 509) :: 37_001] = np.inf
    if nonfinite_pattern == "interval":
        gap_start = max(2, points // 3)
        rows[:, gap_start : min(points - 2, gap_start + max(10, points // 20))] = np.nan
    settings = ScanAcquisitionSettings(
        start_nm,
        stop_nm,
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
    root: Path, points: int, detectors: tuple[Detector, ...], nonfinite_pattern: str
) -> tuple[list[ImportedScan], dict[str, float]]:
    measurements = [
        _synthetic_measurement(
            points,
            detectors if index == 0 or len(detectors) == 1 or index % 2 == 0 else detectors[:-1],
            float(index),
            nonfinite_pattern,
        )
        for index in range(MAX_OVERLAY_SCANS + 1)
    ]
    paths: list[Path] = []
    parse_times: dict[str, float] = {}
    for index in range(MAX_OVERLAY_SCANS + 1):
        payload = build_scan_export_v2(measurements[index], comment="deterministic overlay performance profile")
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
    # Include MAT import cost, but keep each plotted scan's phase/grid distinct.
    del mat_scan
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


def _working_set_mb() -> float | None:
    if sys.platform != "win32":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return None
    return float(counters.WorkingSetSize / (1024 * 1024))


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
    finite_mode: str = "current",
    pen_width: float = 1.4,
    cosmetic: bool = False,
    antialias: bool = False,
    screenshot_dir: Path | None = None,
) -> dict[str, float]:
    timings: defaultdict[str, float] = defaultdict(float)
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
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
                                pen.color(), width=pen_width, style=QtCore.Qt.PenStyle.SolidLine, cosmetic=cosmetic
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
        for overlay in widget.scan_overlays:
            for item in overlay.items.values():
                if finite_mode == "safe-auto":
                    item.setData(
                        item.xData,
                        item.yData,
                        connect="auto",
                        skipFiniteCheck=False,
                    )
                elif finite_mode == "finite-check":
                    item.setData(
                        item.xData,
                        item.yData,
                        connect="finite",
                        skipFiniteCheck=False,
                    )
                elif finite_mode == "current":
                    item.setData(
                        item.xData,
                        item.yData,
                        connect="finite",
                        skipFiniteCheck=True,
                    )
                if dash:
                    pen = item.opts["pen"]
                    style = pen.style()
                    target_width = pen_width
                    target_cosmetic = cosmetic
                    if finite_mode == "current":
                        style = _LEGACY_OVERLAY_LINE_STYLES[overlay.style_index % len(_LEGACY_OVERLAY_LINE_STYLES)]
                        target_width = 1.4 if overlay.style_index < len(_LEGACY_OVERLAY_LINE_STYLES) else 1.8
                        target_cosmetic = False
                    target_pen = pg.mkPen(pen.color(), width=target_width, style=style, cosmetic=target_cosmetic)
                    if style == QtCore.Qt.PenStyle.CustomDashLine:
                        target_pen.setDashPattern(pen.dashPattern())
                    item.setPen(target_pen)
                item.setData(
                    item.xData,
                    item.yData,
                    antialias=antialias,
                    connect=item.opts["connect"],
                    skipFiniteCheck=item.opts["skipFiniteCheck"],
                )
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
        repaint_samples = []
        for _ in range(12):
            start = time.perf_counter()
            widget.grab()
            repaint_samples.append(time.perf_counter() - start)
        timings["repaint_p50_ms"] = float(np.percentile(repaint_samples, 50) * 1000)
        timings["repaint_p95_ms"] = float(np.percentile(repaint_samples, 95) * 1000)
        pan_zoom = []
        for index in range(10):
            start = time.perf_counter()
            widget.plot_widget.plotItem.vb.setRange(xRange=[1540 + index * 0.2, 1550 + index * 0.2])
            QtWidgets.QApplication.processEvents()
            pan_zoom.append(time.perf_counter() - start)
        timings["pan_zoom_p50_ms"] = float(np.percentile(pan_zoom, 50) * 1000)
        timings["pan_zoom_p95_ms"] = float(np.percentile(pan_zoom, 95) * 1000)
        event_lags: list[float] = []
        for _ in range(20):
            start = time.perf_counter()
            QtCore.QTimer.singleShot(0, lambda began=start: event_lags.append(time.perf_counter() - began))
            QtWidgets.QApplication.processEvents()
        timings["event_loop_p50_ms"] = float(np.percentile(event_lags, 50) * 1000) if event_lags else -1.0
        timings["event_loop_p95_ms"] = float(np.percentile(event_lags, 95) * 1000) if event_lags else -1.0
        from PySide6.QtWidgets import QDialog

        def accept_overlay_manager() -> None:
            dialog = next(
                (
                    candidate
                    for candidate in QtWidgets.QApplication.topLevelWidgets()
                    if isinstance(candidate, QDialog) and candidate.windowTitle() == "Manage Scan Overlays"
                ),
                None,
            )
            if dialog is not None:
                dialog.accept()

        QtCore.QTimer.singleShot(0, accept_overlay_manager)
        start = time.perf_counter()
        widget.manage_overlays()
        timings["overlay_manager_open_ms"] = (time.perf_counter() - start) * 1000
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
        if screenshot_dir is not None:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            widget.cursor_label.setVisible(False)
            widget.v_line.setVisible(False)
            widget.h_line.setVisible(False)
            for zoom, wavelength_range in (
                ("overview", [1500.0, 1600.0]),
                ("resonance-zoom", [1548.1, 1549.0]),
            ):
                widget.plot_widget.plotItem.vb.setRange(xRange=wavelength_range, padding=0.02)
                QtWidgets.QApplication.processEvents()
                widget.grab().save(str(screenshot_dir / f"{overlay_count}-overlays-{zoom}.png"))
        widget.close()
        QtWidgets.QApplication.processEvents()
        timings["cpu_time_s"] = time.process_time() - cpu_start
        timings["cpu_utilization_percent"] = timings["cpu_time_s"] / (time.perf_counter() - wall_start) * 100
        memory_mb = _working_set_mb()
        if memory_mb is not None:
            timings["working_set_mb"] = memory_mb
    finally:
        for (target, method), original in saved_methods.items():
            setattr(target, method, original)
    return dict(timings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    parser.add_argument("--detectors", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--overlay-counts", type=int, nargs="+", default=[0, 1, 2, 4, 8])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--solid", action="store_true", help="Use solid rather than normal scan line styles")
    parser.add_argument("--no-downsampling", action="store_true")
    parser.add_argument("--no-legend", action="store_true")
    parser.add_argument("--no-crosshair", action="store_true")
    parser.add_argument("--auto-downsample-factor", type=float, default=1.0)
    parser.add_argument("--mouse-events", action="store_true")
    parser.add_argument("--nonfinite-pattern", choices=("none", "nan", "inf", "mixed", "interval"), default="mixed")
    parser.add_argument("--finite-mode", choices=("current", "safe-auto", "finite-check"), default="safe-auto")
    parser.add_argument("--pen-width", type=float, default=1.0)
    parser.add_argument("--cosmetic", dest="cosmetic", action="store_true")
    parser.add_argument("--non-cosmetic", dest="cosmetic", action="store_false")
    parser.set_defaults(cosmetic=True)
    parser.add_argument("--antialias", action="store_true")
    parser.add_argument("--screenshots-dir", type=Path)
    args = parser.parse_args()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    install_application_fonts(app)
    report: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="iopanel-overlay-profile-") as temp:
        root = Path(temp)
        for points in args.points:
            for detector_count in args.detectors:
                detectors = tuple(Detector(index + 1) for index in range(detector_count))
                scans, parsing = _write_fixtures(root, points, detectors, args.nonfinite_pattern)
                for overlay_count in args.overlay_counts:
                    report.append(
                        {
                            "points_per_scan": points,
                            "detector_count": detector_count,
                            "overlay_count": overlay_count,
                            "nonfinite_pattern": args.nonfinite_pattern,
                            "finite_mode": args.finite_mode,
                            "pen_width": args.pen_width,
                            "cosmetic": args.cosmetic,
                            "antialias": args.antialias,
                            "qt_platform": QtWidgets.QApplication.platformName(),
                            "window_system": sys.platform,
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
                                finite_mode=args.finite_mode,
                                pen_width=args.pen_width,
                                cosmetic=args.cosmetic,
                                antialias=args.antialias,
                                screenshot_dir=args.screenshots_dir,
                            ),
                        }
                    )
    encoded = json.dumps(report, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
