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
APP_NAME = "IOP Lab @ UniFe"
APP_VERSION = "0.2.0"
DEFAULT_LOG_FILE = Path("lab_app.log")
DEFAULT_CONFIG_FILE = Path("config.ini")


def setup_logger(
    log_level: int,
    log_file: Path,
    max_bytes: int,
    backup_count: int,
) -> logging.Logger:
    """Sets up a dedicated logger for the application with rotating file and console handlers."""
    logger_instance = logging.getLogger("LabApp")
    logger_instance.setLevel(log_level)
    if logger_instance.handlers:
        return logger_instance

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
        print(f"Error: Could not set up file logger at '{log_file}': {e}", file=sys.stderr)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(max(log_level, logging.INFO))
    console_formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)
    logger_instance.addHandler(console_handler)

    # Prevent messages from being passed to the root logger, avoiding duplicate output
    logger_instance.propagate = False
    return logger_instance


def global_exception_hook(exctype, value, tb):
    """Global exception hook to catch and log any unhandled exceptions."""
    logger = logging.getLogger("LabApp")
    logger.critical("Unhandled exception occurred:", exc_info=(exctype, value, tb))
    if QApplication.instance():
        error_message = "A critical error occurred and the application must close.\n\n"
        error_message += f"Error: {value}\n\n"
        error_message += "Please check the log file for detailed information."
        QMessageBox.critical(
            None,
            f"{APP_NAME} - Fatal Error",
            error_message,
        )
    sys.__excepthook__(exctype, value, tb)


def load_raw_config_from_ini(config_file: Path) -> dict:
    """Loads an INI file into a raw dictionary without validation."""
    import configparser

    config = configparser.ConfigParser()
    if not config_file.is_file():
        logging.warning(f"Configuration file not found: {config_file}. Using default values.")
        return {}
    try:
        config.read(config_file, encoding="utf-8")
        # Pydantic will handle boolean conversion, so read raw strings
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
    """Application entry point."""
    args = parse_args()

    # Temporarily configure basic logging to catch errors during config loading
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Load the raw dictionary from the .ini file
    raw_config_dict = load_raw_config_from_ini(args.config)

    # Validate and parse the raw dictionary using Pydantic
    try:
        app_config = AppConfig.from_ini_dict(raw_config_dict)
    except ValidationError as e:
        # Pydantic gives beautiful, human-readable errors.
        error_msg = f"Configuration file '{args.config}' is invalid.\n\nErrors:\n{e}"
        logging.critical(error_msg)
        # Show a message box if we can (before the app object is created)
        temp_app_for_msgbox = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Configuration Error", error_msg)
        return 1

    # Determine final logging settings (CLI > config > defaults)
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
            logger.info("Application shutting down...")
            window.cleanup()
            logger.info("Shutdown complete.")
            logging.shutdown()

        app.aboutToQuit.connect(on_shutdown)
        logger.info("Application started successfully. Entering event loop.")
        return app.exec()
    except Exception:
        # global_exception_hook will log this
        return 1


if __name__ == "__main__":
    sys.exit(main())
