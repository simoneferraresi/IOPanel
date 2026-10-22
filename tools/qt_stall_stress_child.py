"""Repeat simulated MainWindow construction and shutdown for Qt CI diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

import app
from app_settings import AppSettings
from config_model import AppConfig
from hardware import ct400_init_worker
from hardware.dummy_ct400 import DummyCT400
from ui import main_window as main_window_module
from ui import plot_widgets


def _config() -> AppConfig:
    config_path = Path(os.environ["IOPANEL_QT_STRESS_CONFIG"])
    return AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))


def _emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, "monotonic": time.monotonic(), **fields}), flush=True)


def main() -> int:
    cycles = int(os.environ.get("IOPANEL_QT_STRESS_CYCLES", "20"))
    if cycles < 1:
        raise ValueError("IOPANEL_QT_STRESS_CYCLES must be positive")

    # The synthetic config is written by the supervisor. Never read the lab config.
    app_instance = QApplication.instance() or QApplication(sys.argv[:1])
    ct400_init_worker.CT400InitWorker._find_dll = lambda _worker: None
    main_window_module.MainWindow._start_vimbasystem = lambda _window: None
    plot_widgets.MATLAB_ENGINE_AVAILABLE = False

    for cycle in range(1, cycles + 1):
        _emit("cycle_started", cycle=cycle, cycles=cycles)
        window = None
        try:
            settings_path = Path(os.environ["IOPANEL_QT_STRESS_CONFIG"]).parent / f"settings-{cycle}.ini"
            settings = AppSettings(QSettings(str(settings_path), QSettings.Format.IniFormat))
            window = main_window_module.MainWindow(_config(), settings=settings)
            window.show()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and (
                len(window.cameras) != len(window.config.cameras)
                or any(panel._latest_pixmap is None for panel in window.camera_panels.values())
                or not isinstance(window.ct400_device, DummyCT400)
            ):
                app_instance.processEvents()
                time.sleep(0.01)
            assert window.camera_panels, "simulated camera placeholder was not constructed"
            assert isinstance(window.ct400_device, DummyCT400), "safe DummyCT400 initialization did not complete"
            assert window.piezo_left is None and window.piezo_right is None, "unexpected piezo initialization"
            assert not window.vmb_instance, "unexpected Vimba system initialization"
            window.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                app_instance.processEvents()
                active = [task for task in window._init_tasks if task.worker_thread.isRunning()]
                if not active and window._shutdown_complete:
                    break
                time.sleep(0.01)
            if any(task.worker_thread.isRunning() for task in window._init_tasks):
                raise AssertionError("initialization task remained active after close")
            assert window._shutdown_complete, "window did not finish asynchronous shutdown"
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert not shiboken6.isValid(window), "native window survived its lifecycle cycle"
            _emit("cycle_completed", cycle=cycle)
        except Exception as exc:  # noqa: BLE001 - retain the failure and shut down this diagnostic cycle.
            _emit("cycle_failed", cycle=cycle, error=repr(exc), traceback=traceback.format_exc())
            if window is not None:
                window.close()
            return 1

    _emit("stress_completed", cycles=cycles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
