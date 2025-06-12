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
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtWidgets import QApplication, QMessageBox

from config_model import AppConfig
from ui.main_window import MainWindow
from ui.theme import APP_STYLESHEET

# Application Metadata
APP_NAME = "IOPanel"
APP_VERSION = "0.3.0"
DEFAULT_LOG_FILE = Path("lab_app.log")
DEFAULT_CONFIG_FILE = Path("config.ini")


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

    Raises:
        OSError: If file logging setup fails, falls back to console logging.
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
    """Global exception hook to catch and log unhandled exceptions.

    This function is assigned to `sys.excepthook` as a last line of defense.
    It logs the full traceback of any uncaught exception to the main application
    logger and displays a user-friendly error message before termination.

    Args:
        exctype: The type of the exception (e.g., ValueError, RuntimeError).
        value: The exception instance containing error details.
        tb: The traceback object providing stack trace information.

    Note:
        Shows an error dialog if the Qt application instance exists.
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

    Uses Python's `configparser` to read an INI file and convert it into
    a dictionary of dictionaries for processing by the Pydantic model.

    Args:
        config_file: The path to the .ini configuration file.

    Returns:
        dict: Dictionary representing INI contents, empty dict if file is
        missing or unparseable.

    Raises:
        configparser.Error: If there are issues parsing the INI file.
    """
    import configparser

    config = configparser.ConfigParser()
    if not config_file.is_file():
        logging.warning(f"Configuration file not found: {config_file}. Using default values.")
        return {}
    try:
        config.read(config_file, encoding="utf-8")
        return {s: dict(config.items(s)) for s in config.sections()}
    except configparser.Error as e:
        logging.error(f"Error parsing config file {config_file}: {e}")
        return {}


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments for the application.

    Defines arguments for:
    - Custom config file location
    - Log level override
    - Log file path override

    Returns:
        argparse.Namespace: Parsed command-line arguments.

    Raises:
        SystemExit: If invalid arguments are provided or --help/--version used.
    """
    parser = argparse.ArgumentParser(description=f"{APP_NAME} GUI Application")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
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
    return parser.parse_args()


def main() -> int:
    """Main application entry point and orchestration.

    Handles the full application lifecycle:
    1. Command-line argument parsing
    2. Configuration loading and validation
    3. Logger setup (file and console handlers)
    4. Global exception hook installation
    5. Qt application initialization and theming
    6. Main window creation and display
    7. Cleanup on application exit

    Returns:
        int: Application exit code (0 for success, 1 for failure).

    Raises:
        ValidationError: If configuration validation fails.
        QApplicationException: If Qt application initialization fails.
    """
    args = parse_args()

    # Temporarily configure basic logging to catch errors during config loading.
    # This will be replaced by the full-featured logger shortly.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Load the raw dictionary from the .ini file
    raw_config_dict = load_raw_config_from_ini(args.config)

    # Validate and parse the raw dictionary using the Pydantic model
    try:
        app_config = AppConfig.from_ini_dict(raw_config_dict)
    except ValidationError as e:
        # Pydantic gives beautiful, human-readable errors.
        error_msg = f"Configuration file '{args.config}' is invalid.\n\nErrors:\n{e}"
        logging.critical(error_msg)
        # Show a message box, creating a temporary QApplication if needed.
        _ = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Configuration Error", error_msg)
        return 1

    # Determine final logging settings (CLI args > config file > model defaults)
    log_level_str = args.log_level or app_config.logging.level
    log_file_path = args.log_file or Path(app_config.logging.file)
    log_level_int = getattr(logging, log_level_str, logging.INFO)

    # Set up the main application logger with the final settings
    logger = setup_logger(
        log_level=log_level_int,
        log_file=log_file_path,
        max_bytes=app_config.logging.max_bytes,
        backup_count=app_config.logging.backup_count,
    )

    # Set the global exception hook AFTER the logger is fully configured
    sys.excepthook = global_exception_hook

    logger.info(f"--- Starting {app_config.app_name} v{APP_VERSION} ---")
    logger.info(f"Using configuration from: {args.config.resolve()}")
    logger.info(f"Log level set to: {log_level_str}")
    logger.info(f"Logging to file: {log_file_path.resolve()}")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName(app_config.app_name)
        app.setApplicationVersion(APP_VERSION)
        app.setStyle("Fusion")
        app.setStyleSheet(APP_STYLESHEET)

        window = MainWindow(config=app_config)
        window.show()

        def on_shutdown():
            """A closure to be called when the application is about to quit."""
            logger.info("Application shutting down...")
            window.cleanup()
            logger.info("Shutdown complete.")
            logging.shutdown()

        # Connect the cleanup function to the application's exit signal
        app.aboutToQuit.connect(on_shutdown)

        logger.info("Application started successfully. Entering event loop.")
        return app.exec()
    except Exception:
        # The global exception hook will log this exception.
        # We return 1 to indicate an error to the operating system.
        return 1


if __name__ == "__main__":
    sys.exit(main())
