"""Driver-free first-responsive MainWindow startup probe for Issue #152.

Invoked only in a fresh child interpreter by tools.performance_baseline.
The real MainWindow is built, but its deferred hardware entry point is patched
*before construction*, and no MATLAB prewarm is scheduled. Qt offscreen timings
are diagnostic event timings, not a claim about a visible desktop frame.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def characterize(output: Path) -> list[dict[str, object]]:
    """Measure app-like imports, GUI setup, paint dispatch and Qt responsiveness."""
    started = time.perf_counter()

    from PySide6.QtCore import QEvent, QObject, QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    qt_imported = time.perf_counter()

    from ui.main_window import MainWindow

    mainwindow_imported = time.perf_counter()

    from app import configure_qt_application
    from app_settings import AppSettings
    from config_model import AppConfig

    imports_finished = time.perf_counter()

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app_created = time.perf_counter()
    configure_qt_application(app)
    app_configured = time.perf_counter()

    # This benchmark must NEVER reach a native instrument worker. Patch the
    # 100-ms deferred entry point before MainWindow captures its bound method.
    events: dict[str, float] = {}
    timed_out = False

    def maybe_done() -> None:
        required = ("first_qt_callback", "first_paint_event", "post_paint_callback", "deferred_init_callback")
        if all(event in events for event in required):
            app.quit()

    def deferred_init_suppressed(_window: MainWindow) -> None:
        events["deferred_init_callback"] = time.perf_counter()
        maybe_done()

    class PaintProbe(QObject):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if event.type() == QEvent.Type.Paint and "first_paint_event" not in events:
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
        app.quit()

    with (
        tempfile.TemporaryDirectory(prefix="iopanel-startup-") as temporary,
        patch.object(MainWindow, "_begin_lazy_init", deferred_init_suppressed),
    ):
        settings_path = Path(temporary) / "isolated-settings.ini"
        settings = AppSettings(QSettings(str(settings_path), QSettings.Format.IniFormat))
        # Explicitly zero enabled cameras; no discovery, hardware SDK or scanner.
        config = AppConfig(instruments={"ct400_backend": "simulation"}, cameras={})
        construction_started = time.perf_counter()
        window = MainWindow(config, settings=settings)
        constructed = time.perf_counter()
        probe = PaintProbe(window)
        window.installEventFilter(probe)
        show_started = time.perf_counter()
        window.show()
        shown = time.perf_counter()
        QTimer.singleShot(0, first_callback)
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(timeout)
        watchdog.start(12_000)
        event_loop_entered = time.perf_counter()
        try:
            app.exec()
        finally:
            watchdog.stop()
            window.removeEventFilter(probe)
            window.close()
            app.processEvents()

    required = ("first_qt_callback", "first_paint_event", "post_paint_callback", "deferred_init_callback")
    if timed_out or any(name not in events for name in required):
        raise RuntimeError(f"Startup probe incomplete: {sorted(events)}; timed_out={timed_out}")
    if not (events["first_paint_event"] <= events["post_paint_callback"]):
        raise AssertionError("Post-paint Qt callback preceded paint dispatch")

    # All numeric durations are from this child's first Python statement;
    # parent subprocess_wall_s also includes interpreter startup and teardown.
    def relative(at: float) -> float:
        return at - started

    row: dict[str, object] = {
        "scenario": "main_window_deferred_hardware_suppressed",
        "qt_platform": app.platformName(),
        "window_system": sys.platform,
        "qt_import_s": qt_imported - started,
        "mainwindow_import_s": mainwindow_imported - qt_imported,
        "remaining_import_s": imports_finished - mainwindow_imported,
        "qapplication_create_s": app_created - imports_finished,
        "qt_configure_s": app_configured - app_created,
        "mainwindow_construct_s": constructed - construction_started,
        "show_call_s": shown - show_started,
        "entry_to_imports_complete_s": relative(imports_finished),
        "entry_to_mainwindow_constructed_s": relative(constructed),
        "entry_to_show_return_s": relative(shown),
        "entry_to_first_qt_callback_s": relative(events["first_qt_callback"]),
        "entry_to_first_paint_event_s": relative(events["first_paint_event"]),
        "entry_to_post_paint_callback_s": relative(events["post_paint_callback"]),
        "entry_to_deferred_init_callback_s": relative(events["deferred_init_callback"]),
        "event_loop_enter_to_first_qt_callback_s": events["first_qt_callback"] - event_loop_entered,
        "event_loop_enter_to_post_paint_callback_s": events["post_paint_callback"] - event_loop_entered,
    }
    if any(isinstance(value, float) and value < 0 for value in row.values()):
        raise AssertionError("Monotonic startup timestamps are inconsistent")
    output.write_text(json.dumps([row], indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return [row]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    characterize(args.output)


if __name__ == "__main__":
    main()
