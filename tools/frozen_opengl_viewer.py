"""Frozen-app OpenGL capability and alignment-viewer diagnostic.

Imported only by the explicit --smoke-opengl-test path. The regular GUI startup
must not initialize OpenGL contexts, display diagnostic windows or generate
these screenshots. The wrapper in app.py keeps the supported invocation stable.
"""

import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox


def run_frozen_opengl_smoke(app: QApplication, log_file: Path, logger: logging.Logger) -> int:
    """Exercise the first real alignment viewer without constructing MainWindow or hardware workers."""
    import time
    from unittest.mock import patch

    import numpy as np
    from OpenGL import GL
    from OpenGL.error import GLError
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
    from PySide6.QtWidgets import QTabWidget, QWidget

    from ui.alignment_panel import AlignmentPanel

    probe_surface = QOffscreenSurface()
    probe_context = QOpenGLContext()
    try:
        probe_surface.create()
        logger.info("SMOKE_OPENGL_PROBE surface_valid=%s", probe_surface.isValid())
        if not probe_surface.isValid():
            probe_surface.destroy()
            reason = "Qt could not create an offscreen OpenGL surface"
            logger.info("SMOKE_OPENGL_UNAVAILABLE %s", {"reason": reason})
            return 0
        context_created = probe_context.create()
        logger.info("SMOKE_OPENGL_PROBE context_created=%s", context_created)
        if not context_created or not probe_context.isValid():
            probe_surface.destroy()
            reason = "Qt could not create a valid offscreen OpenGL context"
            logger.info("SMOKE_OPENGL_UNAVAILABLE %s", {"reason": reason})
            return 0
        if not probe_context.makeCurrent(probe_surface):
            probe_surface.destroy()
            reason = "Qt could not make the offscreen OpenGL context current"
            logger.info("SMOKE_OPENGL_UNAVAILABLE %s", {"reason": reason})
            return 0
        probe_vendor = GL.glGetString(GL.GL_VENDOR)
        probe_renderer = GL.glGetString(GL.GL_RENDERER)
        probe_version = GL.glGetString(GL.GL_VERSION)
        probe_format = probe_context.format()
    except GLError as exc:
        probe_context.doneCurrent()
        probe_surface.destroy()
        logger.info(
            "SMOKE_OPENGL_UNAVAILABLE %s",
            {"reason": "OpenGL context rejected its basic capability query", "error": repr(exc)},
        )
        return 0
    except Exception:
        logger.exception("OpenGL capability probe setup failed")
        probe_context.doneCurrent()
        probe_surface.destroy()
        raise
    probe_details = {
        "vendor": probe_vendor.decode("ascii", errors="replace") if probe_vendor else "unknown",
        "renderer": probe_renderer.decode("ascii", errors="replace") if probe_renderer else "unknown",
        "version": probe_version.decode("ascii", errors="replace") if probe_version else "unknown",
        "major": probe_format.majorVersion(),
        "minor": probe_format.minorVersion(),
        "profile": str(probe_format.profile()),
    }
    try:
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glFinish()
    except GLError as exc:
        probe_context.doneCurrent()
        probe_surface.destroy()
        logger.info("SMOKE_OPENGL_UNAVAILABLE %s", {**probe_details, "reason": repr(exc)})
        return 0
    probe_context.doneCurrent()
    probe_surface.destroy()
    logger.info("SMOKE_OPENGL_CAPABILITY %s", probe_details)
    if probe_format.majorVersion() < 2:
        logger.info(
            "SMOKE_OPENGL_UNAVAILABLE %s",
            {**probe_details, "reason": "PyQtGraph 3D requires an OpenGL 2.0 capable context"},
        )
        return 0

    x = np.linspace(-2.0, 2.0, 5)
    y = np.linspace(-2.0, 2.0, 5)
    z = np.arange(25, dtype=float).reshape(5, 5)
    panel = AlignmentPanel(None, None, None)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Power Monitor")
    tabs.addTab(panel, "Auto Alignment")
    with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
        panel.on_mapping_finished(x, y, z)
    tabs.resize(1100, 700)
    tabs.show()
    QTimer.singleShot(0, lambda: tabs.setCurrentIndex(1))
    started = time.monotonic()
    viewer_identity = None
    completed = False

    def finish(code: int, event: str, **fields: object) -> None:
        nonlocal completed
        if completed:
            return
        completed = True
        logger.info("%s %s", event, fields)
        tabs.close()
        app.exit(code)

    def inspect_first_view() -> None:
        nonlocal viewer_identity
        try:
            viewer = panel._plot3d_widget
            if viewer is None:
                if "3D viewer unavailable:" in panel._plot_placeholder.text():
                    finish(6, "SMOKE_OPENGL_FAILED", reason=panel._plot_placeholder.text())
                elif time.monotonic() - started < 8.0:
                    QTimer.singleShot(100, inspect_first_view)
                else:
                    finish(6, "SMOKE_OPENGL_FAILED", reason="alignment viewer did not initialize")
                return

            viewer_identity = id(viewer)
            if not viewer.view.isValid():
                if time.monotonic() - started < 8.0:
                    QTimer.singleShot(100, inspect_first_view)
                else:
                    finish(0, "SMOKE_OPENGL_UNAVAILABLE", reason="QOpenGLWidget has no valid context")
                return

            context = viewer.view.context()
            if context is None or not context.isValid():
                finish(0, "SMOKE_OPENGL_UNAVAILABLE", reason="Qt could not make the viewer context current")
                return

            viewer.view.makeCurrent()
            vendor = GL.glGetString(GL.GL_VENDOR)
            renderer = GL.glGetString(GL.GL_RENDERER)
            version = GL.glGetString(GL.GL_VERSION)
            context_format = context.format()
            logger.info(
                "SMOKE_OPENGL_CONTEXT vendor=%s renderer=%s version=%s requested=%d.%d profile=%s actual=%d.%d",
                vendor.decode("ascii", errors="replace") if vendor else "unknown",
                renderer.decode("ascii", errors="replace") if renderer else "unknown",
                version.decode("ascii", errors="replace") if version else "unknown",
                viewer.view.format().majorVersion(),
                viewer.view.format().minorVersion(),
                str(viewer.view.format().profile()),
                context_format.majorVersion(),
                context_format.minorVersion(),
            )
            prior_error = GL.glGetError()
            GL.glClearColor(0.05, 0.05, 0.05, 1.0)
            GL.glFinish()
            clear_error = GL.glGetError()
            viewer.view.doneCurrent()
            if prior_error != GL.GL_NO_ERROR or clear_error != GL.GL_NO_ERROR:
                raise RuntimeError(f"OpenGL error before/after glClearColor: {prior_error:#x}/{clear_error:#x}")

            expected_title = "Power Map (Peak: 24.000 mW at X=2.00, Y=2.00 \u00b5m)"
            if viewer.title_label.text() != expected_title:
                raise AssertionError(f"pending map title mismatch: {viewer.title_label.text()!r}")
            if viewer.surface_plot is None or any(
                item not in viewer.view.items
                for item in (
                    viewer.grid_item,
                    viewer.x_axis,
                    viewer.y_axis,
                    viewer.z_axis,
                    viewer.x_label,
                    viewer.y_label,
                    viewer.z_label,
                )
            ):
                raise AssertionError("surface, grid, axis lines, or axis labels are missing")
            if (viewer.x_label.text, viewer.y_label.text, viewer.z_label.text) != (
                "X (\u00b5m)",
                "Y (\u00b5m)",
                "Power (a.u.)",
            ):
                raise AssertionError("3D axis labels are missing or malformed")
            if panel.colorbar_widget.min_label.text() != "0.000 mW":
                raise AssertionError("first-view colorbar minimum was not restored")
            if panel.colorbar_widget.max_label.text() != "24.000 mW":
                raise AssertionError("first-view colorbar maximum was not restored")

            updated = z + 1.0
            with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
                panel.on_mapping_finished(x, y, updated)
            app.processEvents()
            framebuffer = viewer.view.grabFramebuffer()
            if framebuffer.isNull() or framebuffer.width() < 1 or framebuffer.height() < 1:
                raise AssertionError("OpenGL viewer returned an empty framebuffer")
            framebuffer_path = log_file.parent / "smoke-opengl-framebuffer.png"
            if not framebuffer.save(str(framebuffer_path)):
                raise AssertionError("could not save the rendered OpenGL framebuffer")
            screenshot = log_file.parent / "smoke-opengl.png"
            if not viewer.grab().save(str(screenshot)):
                raise AssertionError("could not save the full rendered viewer")
            colorbar_screenshot = log_file.parent / "smoke-opengl-colorbar.png"
            if not panel.colorbar_widget.grab().save(str(colorbar_screenshot)):
                raise AssertionError("could not save the rendered colorbar")
            if viewer.title_label.text() != "Power Map (Peak: 25.000 mW at X=2.00, Y=2.00 \u00b5m)":
                raise AssertionError("viewer title did not update after the next power map")
            if (
                panel.colorbar_widget.min_label.text() != "1.000 mW"
                or panel.colorbar_widget.max_label.text() != "25.000 mW"
            ):
                raise AssertionError("colorbar did not update after the next power map")

            tabs.setCurrentIndex(0)
            app.processEvents()
            tabs.setCurrentIndex(1)
            app.processEvents()
            if tabs.currentIndex() != 1 or id(panel._plot3d_widget) != viewer_identity:
                raise AssertionError("returning to Auto Alignment recreated or failed to restore the viewer")

            finish(
                0,
                "SMOKE_OPENGL_VIEWER_OK",
                vendor=vendor.decode("ascii", errors="replace") if vendor else "unknown",
                renderer=renderer.decode("ascii", errors="replace") if renderer else "unknown",
                version=version.decode("ascii", errors="replace") if version else "unknown",
                profile=str(context.format().profile()),
                screenshot=str(screenshot),
                framebuffer=str(framebuffer_path),
                colorbar=str(colorbar_screenshot),
            )
        except Exception as exc:
            logger.exception("Safe packaged OpenGL viewer smoke failed")
            finish(6, "SMOKE_OPENGL_FAILED", error=repr(exc))

    QTimer.singleShot(100, inspect_first_view)
    result = app.exec()
    logger.info("SMOKE_OPENGL_EXIT code=%d", result)
    return result
