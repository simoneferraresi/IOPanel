"""Hardware-free regression coverage for the extracted OpenGL smoke diagnostic."""

import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType

from PySide6 import QtGui

import app


def test_frozen_opengl_wrapper_delegates_arguments_and_exit_code(monkeypatch, tmp_path):
    """Keep app.py's call contract and defer implementation import until requested."""
    fake_module = ModuleType("tools.frozen_opengl_viewer")
    received = []
    application = object()
    log_file = tmp_path / "opengl-smoke.log"
    logger = logging.getLogger("LabApp.opengl_route_test")

    def fake_diagnostic(qt_app, destination, selected_logger):
        received.append((qt_app, destination, selected_logger))
        return 6

    fake_module.run_frozen_opengl_smoke = fake_diagnostic
    monkeypatch.setitem(sys.modules, "tools.frozen_opengl_viewer", fake_module)

    assert app.run_frozen_opengl_smoke(application, log_file, logger) == 6
    assert received == [(application, log_file, logger)]


def test_extracted_diagnostic_import_does_not_create_opengl_context(monkeypatch):
    """The diagnostic module must be safe to import without running a GL probe."""

    class UnexpectedContext:
        def __init__(self):
            raise AssertionError("Importing the diagnostic created an OpenGL context")

    monkeypatch.setattr(QtGui, "QOpenGLContext", UnexpectedContext)
    module = importlib.import_module("tools.frozen_opengl_viewer")
    assert callable(module.run_frozen_opengl_smoke)


def test_extracted_smoke_reports_unavailable_surface_without_mainwindow(qapp, monkeypatch, caplog, tmp_path):
    """An unavailable GL platform remains a non-error capability result."""

    class InvalidSurface:
        destroyed = False

        def create(self):
            return None

        def isValid(self):
            return False

        def destroy(self):
            self.destroyed = True

    class ContextNeverUsed:
        def __init__(self):
            self.touched = False

        def create(self):
            raise AssertionError("GL context creation was attempted after invalid surface")

    surface = InvalidSurface()
    monkeypatch.setattr(QtGui, "QOffscreenSurface", lambda: surface)
    monkeypatch.setattr(QtGui, "QOpenGLContext", ContextNeverUsed)

    from tools.frozen_opengl_viewer import run_frozen_opengl_smoke

    with caplog.at_level(logging.INFO, logger="LabApp.opengl_probe_test"):
        result = run_frozen_opengl_smoke(
            qapp,
            Path(tmp_path) / "gl.log",
            logging.getLogger("LabApp.opengl_probe_test"),
        )

    assert result == 0
    assert surface.destroyed
    assert "SMOKE_OPENGL_UNAVAILABLE" in caplog.text
    assert "Qt could not create an offscreen OpenGL surface" in caplog.text
