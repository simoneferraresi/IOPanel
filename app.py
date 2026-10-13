"""The main entry point for the IOPanel App.

This script is responsible for:
1.  Parsing command-line arguments to override configuration settings.
2.  Loading and validating the application configuration from `config.ini`
    using Pydantic models for type safety.
3.  Setting up a robust, rotating file logger and a console logger.
4.  Installing a global exception hook to catch and log any unhandled
    exceptions, preventing the application from crashing silently.
5.  Initializing the QApplication, setting the application theme, and
    creating and showing the MainWindow.
6.  Connecting the application's `aboutToQuit` signal to a cleanup
    handler in the MainWindow to ensure graceful shutdown of all hardware
    and threads.
"""

import argparse
import logging
import os
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtWidgets import QApplication, QMessageBox

from app_settings import AppSettings
from config_model import AppConfig, ConfigSemanticError
from ui.log_console import install_log_handler
from ui.main_window import MainWindow
from ui.typography import install_application_fonts

# Application Metadata
APP_NAME = "IOPanel"
APP_VERSION = "0.3.0"
DEFAULT_LOG_FILE = Path("lab_app.log")
DEFAULT_CONFIG_FILE = Path("config.ini")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_data_dir() -> Path:
    """Return a writable per-user directory for mutable application files."""
    root = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        if sys.platform == "win32"
        else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    )
    try:
        result = root / APP_NAME
        result.mkdir(parents=True, exist_ok=True)
        return result
    except OSError:
        result = Path(tempfile.gettempdir()) / APP_NAME
        result.mkdir(parents=True, exist_ok=True)
        return result


def resolve_config_path(path: Path | None) -> Path:
    """Resolve frozen relative configs beside the executable."""
    configured = path or DEFAULT_CONFIG_FILE
    if configured.is_absolute() or not is_frozen():
        return configured
    return Path(sys.executable).resolve().parent / configured


def resolve_log_path(path: Path) -> Path:
    """Resolve frozen relative logs into the writable user data directory."""
    if path.is_absolute() or not is_frozen():
        return path
    return application_data_dir() / path


def is_simulation_smoke_config(app_config: AppConfig) -> bool:
    """Reject smoke configurations that could attempt native camera/CT400 startup."""
    return app_config.instruments.ct400_backend == "simulation" and all(
        not camera.enabled or camera.backend == "simulation" for camera in app_config.cameras.values()
    )


def run_frozen_opengl_smoke(app: QApplication, log_file: Path, logger: logging.Logger) -> int:
    """Exercise the first real alignment viewer without constructing MainWindow or hardware workers."""
    import time
    from unittest.mock import patch

    import numpy as np
    from OpenGL import GL
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QTabWidget, QWidget

    from ui.alignment_panel import AlignmentPanel

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


class ConfigLoadError(Exception):
    """Raised when the requested configuration file cannot be loaded."""


def configure_qt_application(app: QApplication, app_name: str = APP_NAME) -> None:
    """Set application metadata and use Qt's native Fusion widget style."""
    app.setOrganizationName("IOPLab")
    app.setApplicationName("IOPanel")
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    if isinstance(app, QApplication):
        install_application_fonts(app)


def setup_logger(
    log_level: int,
    log_file: Path,
    max_bytes: int,
    backup_count: int,
) -> logging.Logger:
    """Sets up a dedicated, application-wide logger.

    Configures a logger named "LabApp" with both a rotating file handler and a
    console handler. This ensures that logs are saved to a file for debugging
    and that important messages are visible on the console during execution.

    Args:
        log_level: The logging level (e.g., logging.INFO, logging.DEBUG).
        log_file: The path to the log file.
        max_bytes: The maximum size of the log file in bytes before rotation.
        backup_count: The number of old log files to keep.

    Returns:
        The fully configured logging.Logger instance.
    """
    logger_instance = logging.getLogger("LabApp")
    logger_instance.setLevel(log_level)

    # Avoid adding duplicate handlers if this function is ever called more than once.
    if logger_instance.handlers:
        return logger_instance

    # Configure file handler with rotation
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(module)s.%(funcName)s:%(lineno)d] - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        logger_instance.addHandler(file_handler)
    except OSError as e:
        # Fallback to console if file logging fails
        print(f"Error: Could not set up file logger at '{log_file}': {e}", file=sys.stderr)

    # Configure console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(max(log_level, logging.INFO))  # Console shows INFO or higher
    console_formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)
    logger_instance.addHandler(console_handler)

    # Prevent messages from being passed to the root logger, avoiding duplicate output.
    logger_instance.propagate = False
    return logger_instance


def global_exception_hook(exctype, value, tb):
    """A global exception hook to catch and log any unhandled exceptions.

    This function is assigned to `sys.excepthook` to act as a last line of
    defense. It logs the full traceback of any uncaught exception to the main
    application logger and displays a user-friendly error message before
    allowing the application to terminate.

    Args:
        exctype: The type of the exception.
        value: The exception instance.
        tb: The traceback object.
    """
    logger = logging.getLogger("LabApp")
    logger.critical("Unhandled exception occurred:", exc_info=(exctype, value, tb))

    # Show a message box to the user if the Qt Application exists
    if QApplication.instance():
        error_message = (
            "A critical error occurred and the application must close.\n\n"
            f"Error: {value}\n\n"
            "Please check the log file for detailed information."
        )
        QMessageBox.critical(
            None,
            f"{APP_NAME} - Fatal Error",
            error_message,
        )
    # Also call the default excepthook to print to stderr
    sys.__excepthook__(exctype, value, tb)


def load_raw_config_from_ini(config_file: Path) -> dict:
    """Loads an INI file into a raw dictionary without validation.

    This function uses Python's standard `configparser` to read an INI file
    and convert it into a dictionary of dictionaries, suitable for processing
    by the Pydantic model adapter.

    Args:
        config_file: The path to the .ini configuration file.

    Returns:
        A dictionary representing the INI file's contents.

    Raises:
        ConfigLoadError: If the file is missing, unreadable, or invalid INI.
    """
    import configparser

    config = configparser.ConfigParser()
    try:
        contents = config_file.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ConfigLoadError(f"File does not exist: {config_file}") from e
    except (OSError, UnicodeError) as e:
        raise ConfigLoadError(f"Could not read file: {e}") from e

    try:
        config.read_string(contents)
        return {s: dict(config.items(s)) for s in config.sections()}
    except configparser.Error as e:
        raise ConfigLoadError(f"INI parsing failed: {e}") from e


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments for the application.

    Defines arguments for specifying a custom config file, log level, and log
    file path, which can override the settings in the INI file.

    Returns:
        An `argparse.Namespace` object containing the parsed arguments.
    """
    parser = argparse.ArgumentParser(description=f"{APP_NAME} GUI Application")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to the configuration file (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (overrides config file setting)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Path to the log file (overrides config file setting)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-opengl-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    """The main application entry point.

    Orchestrates the application startup sequence:
    1. Parses arguments.
    2. Loads and validates configuration.
    3. Sets up logging.
    4. Initializes and runs the Qt application.

    Returns:
        The exit code of the application. 0 for success, 1 for failure.
    """
    args = parse_args()
    config_path = resolve_config_path(args.config)

    # Temporarily configure basic logging to catch errors during config loading.
    # This will be replaced by the full-featured logger shortly.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Load the raw dictionary from the .ini file before schema validation.
    try:
        raw_config_dict = load_raw_config_from_ini(config_path)
    except ConfigLoadError as e:
        error_msg = f"Configuration file '{config_path}' could not be loaded.\n\n{e}"
        logging.critical(error_msg)  # noqa: LOG015 — bootstrap logging runs before the app logger is configured.
        _ = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Configuration Load Error", error_msg)
        return 1

    # Validate and parse the raw dictionary using the Pydantic model
    try:
        app_config = AppConfig.from_ini_dict(raw_config_dict)
    except ConfigSemanticError as e:
        error_msg = f"Configuration file '{config_path}' has an invalid structure.\n\n{e}"
        logging.critical(error_msg)  # noqa: LOG015 — bootstrap logging runs before the app logger is configured.
        _ = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Configuration Error", error_msg)
        return 1
    except ValidationError as e:
        # Pydantic gives beautiful, human-readable errors.
        error_msg = f"Configuration file '{config_path}' is invalid.\n\nErrors:\n{e}"
        logging.critical(error_msg)  # noqa: LOG015 — bootstrap logging runs before the app logger is configured.
        # Show a message box, creating a temporary QApplication if needed.
        _ = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Configuration Error", error_msg)
        return 1

    if (args.smoke_test or args.smoke_opengl_test) and not is_simulation_smoke_config(app_config):
        logging.getLogger("LabApp").critical(
            "Smoke mode requires simulated CT400 and enabled cameras; refusing hardware startup."
        )
        return 2

    # Determine final logging settings (CLI args > config file > model defaults)
    log_level_str = args.log_level or app_config.logging.level
    log_file_path = resolve_log_path(args.log_file or Path(app_config.logging.file))
    log_level_int = getattr(logging, log_level_str, logging.INFO)

    # Set up the main application logger with the final settings
    logger = setup_logger(
        log_level=log_level_int,
        log_file=log_file_path,
        max_bytes=app_config.logging.max_bytes,
        backup_count=app_config.logging.backup_count,
    )
    # Install before startup messages so the in-app console can replay them.
    install_log_handler(logger)

    # Set the global exception hook AFTER the logger is fully configured
    sys.excepthook = global_exception_hook

    logger.info(f"--- Starting {app_config.app_name} v{APP_VERSION} ---")
    logger.info(f"Using configuration from: {config_path.resolve()}")
    logger.info(f"Log level set to: {log_level_str}")
    logger.info(f"Logging to file: {log_file_path.resolve()}")

    try:
        app = QApplication(sys.argv)
        configure_qt_application(app, app_config.app_name)

        if args.smoke_test:
            from PySide6.QtCore import QTimer

            from ui.application_icons import application_icon
            from ui.typography import _FONT_FAMILIES

            icon = application_icon("optical_burst")
            if icon.isNull() or not _FONT_FAMILIES["sans"] or not _FONT_FAMILIES["mono"]:
                logger.error("Smoke resource verification failed: bundled icon or fonts unavailable.")
                return 3
            logger.info("SMOKE_RESOURCES_OK icon=optical_burst fonts=%s,%s", *_FONT_FAMILIES.values())

        if args.smoke_opengl_test:
            return run_frozen_opengl_smoke(app, log_file_path, logger)

        window = MainWindow(config=app_config, settings=AppSettings())
        window.log_console.log_file = log_file_path
        window.show()
        if args.smoke_test:

            def close_when_initialized():
                if not window._lazy_init_started or window._init_tasks:
                    QTimer.singleShot(100, close_when_initialized)
                    return
                else:
                    expected_cameras = sum(cam.enabled for cam in app_config.cameras.values())
                    actual_cameras = len(window.cameras)
                    logger.info("SMOKE_SIMULATION_READY cameras=%d/%d", actual_cameras, expected_cameras)
                    window.close()
                    if actual_cameras != expected_cameras:
                        logger.error("Smoke camera initialization incomplete.")
                        app.exit(4)

            QTimer.singleShot(100, close_when_initialized)
        else:
            window.plot_widget.schedule_matlab_prewarm()

        about_to_quit_at: float | None = None

        def on_shutdown():
            """A closure to be called when the application is about to quit."""
            nonlocal about_to_quit_at
            about_to_quit_at = time.perf_counter()
            logger.info("Qt aboutToQuit received; interpreter cleanup follows.")

        # Connect the cleanup function to the application's exit signal
        app.aboutToQuit.connect(on_shutdown)

        logger.info("Application started successfully. Entering event loop.")

        exit_code = app.exec()
        if about_to_quit_at is not None:
            logger.info("Qt event loop returned %.2f s after aboutToQuit.", time.perf_counter() - about_to_quit_at)
        return exit_code
    except Exception:  # noqa: BLE001  # Preserve the application error-to-exit-code fallback.
        # The global exception hook will log this exception.
        # We return 1 to indicate an error to the operating system.
        return 1


if __name__ == "__main__":
    sys.exit(main())
