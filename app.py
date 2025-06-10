import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path  ## R-1: Import Path for modern path handling
from typing import Any

from PySide6.QtWidgets import QApplication, QMessageBox  ## R-4: Import QMessageBox

from config_model import AppConfig
from ui.main_window import MainWindow
from ui.theme import APP_STYLESHEET

# Application Metadata
APP_NAME = "IOP Lab @ UniFe"
APP_VERSION = "0.2.0"

## R-1: Use Path objects for default file paths for consistency.
DEFAULT_LOG_FILE = Path("lab_app.log")
DEFAULT_CONFIG_FILE = Path("config.ini")


def setup_logger(
    log_level: int,
    log_file: Path,
    max_bytes: int,
    backup_count: int,
) -> logging.Logger:
    """
    Sets up a dedicated logger for the application with rotating file and console handlers.

    Args:
        log_level: The logging level (e.g., logging.DEBUG, logging.INFO).
        log_file: Path object for the log file.
        max_bytes: Maximum size of the log file before rotation.
        backup_count: Number of backup log files to keep.

    Returns:
        The configured logger instance.
    """
    logger_instance = logging.getLogger("LabApp")
    logger_instance.setLevel(log_level)

    # Prevent adding handlers multiple times if this function is ever called again.
    if logger_instance.handlers:
        return logger_instance

    # File handler (Rotating)
    try:
        ## R-3: Use pathlib to ensure the parent directory exists. Cleaner and more robust.
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
        # If file logging fails, we still want the console logger.
        # We print the error and continue.
        print(
            f"Error: Could not set up file logger at '{log_file}': {e}", file=sys.stderr
        )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    # Set console level to INFO unless the global level is higher (e.g., WARNING)
    console_handler.setLevel(max(log_level, logging.INFO))
    console_formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)
    logger_instance.addHandler(console_handler)

    return logger_instance


def global_exception_hook(exctype, value, tb):
    """
    Global exception hook to catch and log any unhandled exceptions.
    Logs the exception and attempts to show a critical error message box to the user.
    """
    logger = logging.getLogger("LabApp")
    logger.critical("Unhandled exception occurred:", exc_info=(exctype, value, tb))

    ## R-4: Attempt to show a user-facing error message as a last resort.
    # This is crucial for notifying the user of a crash that happens after the event loop starts.
    # We check if a QApplication instance exists, as it's needed for QMessageBox.
    if QApplication.instance():
        error_message = "A critical error occurred and the application must close.\n\n"
        error_message += f"Error: {value}\n\n"
        error_message += "Please check the log file for detailed information."
        QMessageBox.critical(
            None,
            f"{APP_NAME} - Fatal Error",
            error_message,
        )

    # Call the default hook which prints the traceback to stderr and exits.
    sys.__excepthook__(exctype, value, tb)


def load_config(config_file: Path) -> dict[str, Any]:
    """
    Loads application configuration from an INI file into a raw dictionary.
    """
    ## R-1: Use configparser directly from the standard library.
    import configparser

    config = configparser.ConfigParser()

    if not config_file.is_file():
        logging.warning(
            f"Configuration file not found: {config_file}. Using default values."
        )
        return {}

    try:
        config.read(config_file, encoding="utf-8")
        # Convert the configparser object to a standard dictionary.
        return {s: dict(config.items(s)) for s in config.sections()}
    except configparser.Error as e:
        logging.error(f"Error parsing config file {config_file}: {e}")
        return {}


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description=f"{APP_NAME} GUI Application")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help=f"Path to the configuration file (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,  # Convert to uppercase immediately
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (overrides config file setting)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Path to the log file (overrides config file setting)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {APP_VERSION}"
    )
    return parser.parse_args()


def main() -> int:
    """Application entry point."""
    # 1. Parse command-line arguments. This has the highest priority.
    args = parse_args()

    # 2. Load configuration from the file specified (or default).
    # We do this before setting up the logger so the logger can use config values.
    # A temporary basic logger is used by load_config if needed.
    logging.basicConfig(level=logging.INFO)  # Basic config for the next step
    raw_config_dict = load_config(args.config)
    app_config = AppConfig.from_dict(raw_config_dict)

    # 3. Determine final logging settings (CLI > config > defaults)
    ## R-2: Centralize the precedence logic. It's much clearer.
    log_level_str = args.log_level or app_config.logging.level
    log_file_path = args.log_file or Path(app_config.logging.file)
    log_level_int = getattr(logging, log_level_str, logging.INFO)

    # 4. Set up the main application logger with the final settings.
    logger = setup_logger(
        log_level=log_level_int,
        log_file=log_file_path,
        max_bytes=app_config.logging.max_bytes,
        backup_count=app_config.logging.backup_count,
    )

    # 5. Set the global exception hook AFTER the logger is fully configured.
    sys.excepthook = global_exception_hook

    logger.info(f"--- Starting {APP_NAME} v{APP_VERSION} ---")
    logger.info(f"Using configuration from: {args.config.resolve()}")
    logger.info(f"Log level set to: {log_level_str}")
    logger.info(f"Logging to file: {log_file_path.resolve()}")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setStyle("Fusion")
        app.setStyleSheet(APP_STYLESHEET)

        window = MainWindow(config=app_config)
        window.show()

        def on_shutdown():
            logger.info("Application shutting down...")
            window.cleanup()  # MainWindow's cleanup method
            logger.info("Shutdown complete.")
            logging.shutdown()

        app.aboutToQuit.connect(on_shutdown)

        logger.info("Application started successfully. Entering event loop.")
        return app.exec()

    except Exception:
        # This will catch exceptions during QApplication or MainWindow instantiation.
        # The global_exception_hook will handle logging.
        # The return code indicates failure.
        return 1


if __name__ == "__main__":
    sys.exit(main())
