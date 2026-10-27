"""Driver-free MainWindow startup probe, run in a fresh Python subprocess.

The first application import follows the production app.py path; native devices
and MATLAB Engine startup are forbidden. Paint timestamps represent Qt event
dispatch, NOT display-compositor presentation or actual operator response.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import override
from unittest.mock import patch


def characterize(
    output: Path,
    *,
    parent_started: float,
    watchdog_ms: int = 12_000,
    record_paint: bool = True,
    schedule_first_callback: bool = True,
) -> list[dict[str, object]]:
    """Timestamp startup from the parent's pre-Popen clock and inside the child."""
    started = time.perf_counter()
    if not math.isfinite(parent_started) or parent_started <= 0 or parent_started > started:
        raise ValueError("Expected a valid parent process-request timestamp")
    if watchdog_ms < 1:
        raise ValueError("Expected a positive Qt watchdog interval")

    # Production entry point imports PySide6, settings, configuration, UI,
    # optional VmbPy/MATLAB bindings in this order. Do not pre-import Qt or
    # ui.main_window: that would distort the import attribution.
    import app as app_module

    app_imported = time.perf_counter()

    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    from config_model import InstrumentsConfig
    from hardware.camera import VIMBA_AVAILABLE
    from ui.plot_widgets import MATLAB_ENGINE_AVAILABLE

    probe_imports_done = time.perf_counter()

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app_created = time.perf_counter()
    app_module.configure_qt_application(app)
    app_configured = time.perf_counter()

    # The UI is real, but its hardware start hooks must never execute.
    # Importing an optional Python adapter above does NOT authorize opening it.
    def forbid_hardware(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Startup benchmark must not enter a device or MATLAB startup path")

    events: dict[str, float] = {}
    timed_out = False

    def maybe_done() -> None:
        required = ("first_qt_callback", "first_paint_event", "post_paint_callback", "deferred_init_callback")
        if all(key in events for key in required):
            window.close()

    def deferred_init_suppressed(_window: object) -> None:
        events["deferred_init_callback"] = time.perf_counter()
        maybe_done()

    class PaintProbe(QObject):
        @override
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if record_paint and event.type() == QEvent.Type.Paint and "first_paint_event" not in events:
                events["first_paint_event"] = time.perf_counter()

                def after_paint() -> None:
                    events["post_paint_callback"] = time.perf_counter()
                    maybe_done()

                QTimer.singleShot(0, after_paint)
            return False

    def first_callback() -> None:
        events["first_qt_callback"] = time.perf_counter()
        maybe_done()

    def timeout() -> None:
        nonlocal timed_out
        timed_out = True
        window.close()

    with (
        tempfile.TemporaryDirectory(prefix="iopanel-startup-") as temporary,
        patch.object(app_module.MainWindow, "_begin_lazy_init", deferred_init_suppressed),
        patch.object(app_module.MainWindow, "_init_ct400_lazy", forbid_hardware),
        patch.object(app_module.MainWindow, "_init_cameras_lazy", forbid_hardware),
        patch.object(app_module.MainWindow, "_start_vimbasystem", forbid_hardware),
        patch.object(app_module.MainWindow, "_init_piezos_lazy", forbid_hardware),
    ):
        settings = app_module.AppSettings(
            QSettings(str(Path(temporary) / "isolated-settings.ini"), QSettings.Format.IniFormat)
        )
        config = app_module.AppConfig(instruments=InstrumentsConfig(ct400_backend="simulation"), cameras={})
        construction_started = time.perf_counter()
        window = app_module.MainWindow(config, settings=settings)
        window.shutdown_complete.connect(lambda: QTimer.singleShot(0, app.quit))
        constructed = time.perf_counter()
        probe = PaintProbe(window)
        window.installEventFilter(probe)
        show_started = time.perf_counter()
        window.show()
        shown = time.perf_counter()
        if schedule_first_callback:
            QTimer.singleShot(0, first_callback)
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(timeout)
        watchdog.start(watchdog_ms)
        event_loop_entered = time.perf_counter()
        try:
            app.exec()
        finally:
            watchdog.stop()
            window.removeEventFilter(probe)
            retirement = QEventLoop()
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(retirement.quit)
            window.histogram_control.shutdown_complete.connect(retirement.quit)
            window.close()
            if not window.histogram_control.shutdown_finished:
                deadline.start(5000)
                retirement.exec()
                deadline.stop()
            if not window.histogram_control.shutdown_finished:
                raise RuntimeError("Startup probe monitor worker did not retire")
            if not window.close():
                raise RuntimeError("Startup probe window did not accept close after retirement")
            window.deleteLater()
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    required = ("first_qt_callback", "first_paint_event", "post_paint_callback", "deferred_init_callback")
    if timed_out or any(name not in events for name in required):
        raise RuntimeError(f"Startup probe incomplete: {sorted(events)}; timed_out={timed_out}")
    if events["first_paint_event"] > events["post_paint_callback"]:
        raise AssertionError("Post-paint queued callback preceded paint dispatch")

    def since(origin: float, at: float) -> float:
        return at - origin

    row: dict[str, object] = {
        "scenario": "main_window_deferred_hardware_suppressed",
        "qt_platform": app.platformName(),
        "window_system": sys.platform,
        "vmbpy_import_available": VIMBA_AVAILABLE,
        "matlab_engine_import_available": MATLAB_ENGINE_AVAILABLE,
        "request_to_child_probe_s": since(parent_started, started),
        "request_to_app_import_complete_s": since(parent_started, app_imported),
        "request_to_mainwindow_constructed_s": since(parent_started, constructed),
        "request_to_show_return_s": since(parent_started, shown),
        "request_to_first_qt_callback_s": since(parent_started, events["first_qt_callback"]),
        "request_to_first_paint_event_s": since(parent_started, events["first_paint_event"]),
        "request_to_post_paint_callback_s": since(parent_started, events["post_paint_callback"]),
        "request_to_deferred_init_callback_s": since(parent_started, events["deferred_init_callback"]),
        "app_module_import_s": since(started, app_imported),
        "probe_post_app_import_s": since(app_imported, probe_imports_done),
        "qapplication_create_s": since(probe_imports_done, app_created),
        "qt_configure_s": since(app_created, app_configured),
        "mainwindow_construct_s": since(construction_started, constructed),
        "show_call_s": since(show_started, shown),
        "entry_to_app_import_complete_s": since(started, app_imported),
        "entry_to_mainwindow_constructed_s": since(started, constructed),
        "entry_to_show_return_s": since(started, shown),
        "entry_to_first_qt_callback_s": since(started, events["first_qt_callback"]),
        "entry_to_first_paint_event_s": since(started, events["first_paint_event"]),
        "entry_to_post_paint_callback_s": since(started, events["post_paint_callback"]),
        "entry_to_deferred_init_callback_s": since(started, events["deferred_init_callback"]),
        "event_loop_enter_to_first_qt_callback_s": since(event_loop_entered, events["first_qt_callback"]),
        "event_loop_enter_to_post_paint_callback_s": since(event_loop_entered, events["post_paint_callback"]),
    }
    output.write_text(json.dumps([row], indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return [row]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-started", type=float, required=True, help="Parent perf_counter before Popen")
    args = parser.parse_args()
    characterize(args.output, parent_started=args.parent_started)


if __name__ == "__main__":
    main()
