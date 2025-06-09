import argparse
import configparser
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

from PySide6.QtWidgets import QApplication

from main_window import (
    MainWindow,
)
from styles import APP_STYLESHEET

# Application Metadata
APP_NAME = "IOP Lab @ UniFe"
APP_VERSION = "0.2.0"

DEFAULT_LOG_FILE = "lab_app.log"
DEFAULT_CONFIG_FILE = "config.ini"


def setup_logger(
    log_level: int = logging.INFO,
    log_file: str = DEFAULT_LOG_FILE,
    file_mode: str = "w",
    max_bytes: int = 5 * 1024 * 1024,  # 5 MB
    backup_count: int = 3,
) -> logging.Logger:
    """
    Sets up a dedicated logger for the application with rotating file and console handlers.

    Args:
        log_level: The logging level (e.g., logging.DEBUG, logging.INFO).
        log_file: Path to the log file.
        file_mode: File mode for the log file ('w' to overwrite, 'a' to append).
                   Note: RotatingFileHandler manages overwriting based on size/backups.
        max_bytes: Maximum size of the log file before rotation.
        backup_count: Number of backup log files to keep.

    Returns:
        The configured logger instance.
    """
    logger_instance = logging.getLogger("LabApp")  # Use a specific name
    logger_instance.setLevel(log_level)

    # Prevent adding handlers multiple times if called again
    if not logger_instance.handlers:
        # File handler (Rotating)
        try:
            # Ensure log directory exists
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Use RotatingFileHandler
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                mode=file_mode,  # mode is less relevant here now
                encoding="utf-8",
            )
            file_handler.setLevel(log_level)
            # More detailed format for file logs
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s"
            )
            file_handler.setFormatter(file_formatter)
            logger_instance.addHandler(file_handler)
        except (OSError, IOError) as e:
            print(f"Error setting up file logger: {e}", file=sys.stderr)
            # Fallback: log critical errors to console even if file logging fails
            log_level = logging.CRITICAL

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        # Set console handler level potentially higher (e.g., INFO) even if file is DEBUG
        console_handler.setLevel(max(log_level, logging.INFO))
        console_formatter = logging.Formatter(
            "%(levelname)s: %(message)s"
        )  # Simpler format
        console_handler.setFormatter(console_formatter)
        logger_instance.addHandler(console_handler)

    return logger_instance


def global_exception_hook(exctype, value, tb):
    """
    Global exception hook to catch and log any unhandled exceptions.

    Logs the exception to the 'LabApp' logger and then calls the default
    excepthook to ensure standard error reporting occurs.
    """
    try:
        logger = logging.getLogger("LabApp")
        logger.critical("Unhandled exception occurred", exc_info=(exctype, value, tb))
    except Exception as e:
        # Fallback if logger itself fails
        print(f"Logging failed during exception hook: {e}", file=sys.stderr)
        import traceback

        traceback.print_exception(exctype, value, tb)  # Print manually

    # Call the default sys excepthook to exit or display error as usual
    sys.__excepthook__(exctype, value, tb)


def load_config(config_file: str) -> Dict[str, Any]:
    """
    Loads application configuration from an INI file.

    Args:
        config_file: Path to the configuration file.

    Returns:
        A dictionary containing the configuration settings. Returns an empty
        dict if the file doesn't exist or is invalid.
    """
    config = configparser.ConfigParser()
    app_config = {}
    if os.path.exists(config_file):
        try:
            config.read(config_file, encoding="utf-8")
            for section in config.sections():
                app_config[section] = {}
                for key, value in config.items(section):
                    # Basic type inference (can be expanded)
                    if value.lower() in ["true", "yes", "on"]:
                        app_config[section][key] = True
                    elif value.lower() in ["false", "no", "off"]:
                        app_config[section][key] = False
                    elif value.isdigit():
                        app_config[section][key] = int(value)
                    else:
                        try:
                            app_config[section][key] = float(value)
                        except ValueError:
                            app_config[section][key] = value  # Keep as string
            logging.getLogger("LabApp").info(f"Loaded configuration from {config_file}")
        except configparser.Error as e:
            logging.getLogger("LabApp").error(
                f"Error parsing config file {config_file}: {e}"
            )
        except Exception as e:
            logging.getLogger("LabApp").error(
                f"Unexpected error loading config file {config_file}: {e}"
            )
    else:
        logging.getLogger("LabApp").warning(
            f"Configuration file not found: {config_file}. Using defaults."
        )

    return app_config


def parse_args() -> argparse.Namespace:
    """
    Parses command-line arguments for configurable options.

    Includes options for log level, log file path, and config file path.

    Returns:
        An argparse.Namespace object containing the parsed arguments.
    """
    parser = argparse.ArgumentParser(description=f"{APP_NAME} GUI Application")
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,  # Default to None to check config file first
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (overrides config file)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,  # Default to None to check config file first
        help=f"Path to the log file (overrides config file, default: {DEFAULT_LOG_FILE})",
    )
    # file-mode is less relevant with RotatingFileHandler, but kept for compatibility
    parser.add_argument(
        "--file-mode",
        type=str,
        default=None,  # Default to None to check config file first
        choices=["w", "a"],
        help="Initial file mode for the log file: 'w' or 'a' (overrides config file)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG_FILE,
        help=f"Path to the configuration file (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {APP_VERSION}"
    )
    return parser.parse_args()


def main() -> int:
    """
    Entry point for the GUI application.

    Sets up logging, configuration, exception handling, the QApplication,
    the main window, and starts the event loop.

    Returns:
        Application exit code (0 for success, non-zero for errors).
    """
    args = parse_args()
    app_config = load_config(args.config)

    # Determine configuration precedence: Defaults < Config File < Command Line Args
    log_level_str = args.log_level or app_config.get("Logging", {}).get("level", "INFO")
    log_file_path = args.log_file or app_config.get("Logging", {}).get(
        "file", DEFAULT_LOG_FILE
    )
    file_mode_val = args.file_mode or app_config.get("Logging", {}).get(
        "mode", "a"
    )  # Default to append for rotation

    # Convert log level string to logging constant
    log_level_int = getattr(logging, log_level_str.upper(), logging.INFO)

    # Setup logger *after* loading config and parsing args
    logger = setup_logger(
        log_level=log_level_int, log_file=log_file_path, file_mode=file_mode_val
    )
    logger.info(f"Starting {APP_NAME} v{APP_VERSION}")
    logger.info(f"Using log level: {log_level_str}")
    logger.info(f"Logging to file: {log_file_path}")

    # Set the global exception hook *after* logger is configured
    sys.excepthook = global_exception_hook

    try:
        # Initialize the QApplication
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setStyle("Fusion")  # Keep Fusion style
        app.setStyleSheet(APP_STYLESHEET)  # Apply global styles

        # --- Create and show the main window ---
        # Pass the loaded configuration to the MainWindow
        window = MainWindow(config=app_config)
        window.show()

        # --- Connect signals for cleanup ---
        def cleanup():
            logger.info("Application shutting down...")
            # Ensure MainWindow cleanup is called if it exists
            if hasattr(window, "cleanup"):
                try:
                    window.cleanup()
                    logger.info("Main window cleanup method executed.")
                except Exception as e:
                    logger.error(
                        f"Error during main window cleanup: {e}", exc_info=True
                    )
            logger.info("Shutdown process finished.")
            logging.shutdown()  # Ensure all handlers are flushed/closed

        app.aboutToQuit.connect(cleanup)

        logger.info("Application started successfully. Entering event loop.")
        # Start the event loop
        exit_code = app.exec()
        logger.info(f"Application exited with code {exit_code}.")
        return exit_code

    except Exception as e:
        # Catch exceptions during app setup or main window creation
        logger.critical(
            "An unexpected error occurred during application startup or runtime.",
            exc_info=True,
        )
        # Attempt to show an error message if possible (QApplication might not be stable)
        try:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(
                None,
                f"{APP_NAME} Fatal Error",
                f"A critical error occurred:\n{e}\n\nPlease check the log file:\n{log_file_path}",
            )
        except Exception as msg_e:
            print(f"Failed to show error message box: {msg_e}", file=sys.stderr)
        return 1  # Non-zero return code indicates an error


if __name__ == "__main__":
    sys.exit(main())
