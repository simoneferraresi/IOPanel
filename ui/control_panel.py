import logging
from ctypes import create_string_buffer

import numpy as np
from PySide6 import QtCore, QtGui
from PySide6.QtCore import (
    QMetaObject,
    QMutex,
    QMutexLocker,
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config_model import AppConfig
from hardware.ct400 import CT400Error
from hardware.ct400_types import (
    Detector,
    Enable,
    LaserInput,
    PowerData,
)
from hardware.interfaces import AbstractCT400
from ui.constants import (
    ID_CT400_MONITOR_PANEL,
    ID_CT400_SCAN_PANEL,
    ID_MONITOR_BUTTON,
    ID_SCAN_BUTTON,
    PROP_MONITORING,
    PROP_SCANNING,
)
from ui.theme import CT400_CONTROL_PANEL_STYLE

MONITOR_TIMER_INTERVAL_MS = 250
logger = logging.getLogger("LabApp.control_panel")


###############################################################################
# ScanSettings
###############################################################################
class ScanSettings:
    def __init__(self):
        self.resolution: str = "N/A"
        self.motor_speed: str = "N/A"
        self.laser_power: str = "N/A"
        self.power_unit: str = ""


###############################################################################
# ScanWorker
###############################################################################
# --- REFACTOR: Inherit from QObject for the recommended worker-thread pattern ---
class ScanWorker(QtCore.QObject):
    _ERROR_BUFFER_SIZE = 1024
    _LASER_COMMAND_DELAY_MS = 150
    _SCAN_POLL_INTERVAL_MS = 100

    completed_signal = QtCore.Signal(np.ndarray, np.ndarray, float)
    progress_signal = QtCore.Signal(int)
    error_signal = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(
        self,
        ct400: AbstractCT400,
        start_wl: float,
        end_wl: float,
        resolution: int,
        laser_power: float,
        input_port: LaserInput,
        disable_wl: float = 1550.0,
        disable_power: float = 1.0,
        parent: QtCore.QObject | None = None,
    ):
        super().__init__(parent)
        if ct400 is None:
            raise ValueError("ScanWorker requires a valid CT400 instance.")
        self.ct400 = ct400
        self.start_wl = start_wl
        self.end_wl = end_wl
        self.resolution = resolution
        self.laser_power = laser_power
        self.input_port = input_port
        self.disable_wl = disable_wl
        self.disable_power = disable_power
        self._running = True

    @Slot()
    def do_scan(self):
        error_buf = create_string_buffer(self._ERROR_BUFFER_SIZE)
        scan_started = False
        try:
            if self._running:
                logger.info(
                    f"ScanWorker: Ensuring laser is disabled on input {self.input_port.name} before starting scan operations."
                )
                self.ct400.cmd_laser(
                    laser_input=self.input_port,
                    enable=Enable.DISABLE,
                    wavelength=float(self.disable_wl),
                    power=float(self.disable_power),
                )
                logger.info(f"ScanWorker: Defensive laser disable command sent for input {self.input_port.name}.")
                QThread.msleep(self._LASER_COMMAND_DELAY_MS)
            else:
                logger.info("ScanWorker: Run started but worker already stopped. Aborting.")
                self.error_signal.emit("Scan aborted before start.")
                return

            if not self._running:
                raise CT400Error("Scan cancelled before set_scan")
            logger.info(
                f"ScanWorker: Setting up scan from {self.start_wl}nm to {self.end_wl}nm, Res: {self.resolution}pm, Power: {self.laser_power}mW"
            )
            self.ct400.set_scan(self.laser_power, self.start_wl, self.end_wl)
            if not self._running:
                raise CT400Error("Scan cancelled after set_scan")
            self.ct400.set_sampling_res(self.resolution)
            if not self._running:
                raise CT400Error("Scan cancelled after set_sampling_res")

            self.ct400.start_scan()
            scan_started = True
            logger.info("ScanWorker: CT400 scan started.")

            while self._running:
                error_status = self.ct400.scan_wait_end(error_buf)
                if error_status == 0:
                    logger.info("ScanWorker: Scan completed successfully.")
                    self.progress_signal.emit(100)
                    break
                elif error_status < 0:
                    error_msg = error_buf.value.decode(errors="ignore")
                    logger.error(f"ScanWorker: Scan error (ScanWaitEnd): {error_msg} (Code: {error_status})")
                    raise CT400Error(f"Scan failed: {error_msg}")
                QThread.msleep(self._SCAN_POLL_INTERVAL_MS)

            if not self._running:
                logger.info("ScanWorker: Scan cancelled by user.")
                raise CT400Error("Scan cancelled by user.")

            logger.info("ScanWorker: Retrieving data points...")
            detectors_to_get = [Detector.DE_1]
            wavelengths, powers_scan_data = self.ct400.get_data_points(detectors_to_get)
            logger.info(f"ScanWorker: Data retrieved. WL: {len(wavelengths)}, Power Shape: {powers_scan_data.shape}")
            log_tail_count = min(100, len(wavelengths))
            if log_tail_count > 0:
                logger.info(f"  ScanWorker Wavelengths (last {log_tail_count}):\n{wavelengths[-log_tail_count:]}")
                if (
                    powers_scan_data.ndim == 2
                    and powers_scan_data.shape[0] > 0
                    and powers_scan_data.shape[1] >= log_tail_count
                ):
                    logger.info(
                        f"  ScanWorker Powers (Det 0, last {log_tail_count}):\n{powers_scan_data[0, -log_tail_count:]}"
                    )
                elif powers_scan_data.ndim == 1 and len(powers_scan_data) >= log_tail_count:
                    logger.info(
                        f"  ScanWorker Powers (1D, last {log_tail_count}):\n{powers_scan_data[-log_tail_count:]}"
                    )
                elif powers_scan_data.size > 0:
                    logger.info(
                        f"  ScanWorker Powers (Det 0, all points as less than {log_tail_count}):\n{powers_scan_data[0, :] if powers_scan_data.ndim == 2 else powers_scan_data[:]}"
                    )
            final_pout = None
            try:
                final_power_reading = self.ct400.get_all_powers()
                final_pout = getattr(final_power_reading, "pout", None)
            except Exception as e:
                logger.warning(f"ScanWorker: Could not get final Pout: {e}")
            self.completed_signal.emit(wavelengths, powers_scan_data, final_pout)
        except CT400Error as e:
            logger.error(f"ScanWorker: CT400 Error: {e}")
            self.error_signal.emit(str(e))
        except Exception as e:
            logger.exception(f"ScanWorker: Unexpected error: {e}")
            self.error_signal.emit(f"Unexpected scan error: {e}")
        finally:
            try:
                if scan_started:
                    self.ct400.stop_scan()
                self.ct400.cmd_laser(
                    laser_input=self.input_port,
                    enable=Enable.DISABLE,
                    wavelength=float(self.disable_wl),
                    power=float(self.disable_power),
                )
            except Exception as e:
                logger.error(f"ScanWorker: Error during cleanup in finally block: {e}")
            logger.info("ScanWorker: Finished.")
            self.finished.emit()

    def stop(self):
        logger.info("ScanWorker: Stop requested.")
        self._running = False


###############################################################################
# PowerFetchWorker
###############################################################################
class PowerFetchWorker(QObject):
    data_ready = Signal(PowerData)
    error_occurred = Signal(str)

    def __init__(self, ct400_device: AbstractCT400 | None, parent: QObject | None = None):
        super().__init__(parent)
        self.ct400 = ct400_device
        self._is_running_lock = QMutex()
        self._is_actually_running = True
        self._is_busy = False
        self._busy_lock = QMutex()

    def is_worker_running(self) -> bool:
        with QMutexLocker(self._is_running_lock):
            return self._is_actually_running

    @Slot()
    def fetch_power(self):
        with QMutexLocker(self._busy_lock):
            if self._is_busy:
                return
            self._is_busy = True

        if not self.is_worker_running() or QThread.currentThread().isInterruptionRequested():
            with QMutexLocker(self._busy_lock):
                self._is_busy = False
            return

        if not self.ct400:
            if self.is_worker_running():
                self.error_occurred.emit("CT400 device is not available in worker.")
            return

        try:
            power_data_tuple: PowerData = self.ct400.get_all_powers()
            if self.is_worker_running():
                self.data_ready.emit(power_data_tuple)
        except CT400Error as e:
            if self.is_worker_running():
                self.error_occurred.emit(f"CT400 Error: {e}")
        except Exception as e:
            if self.is_worker_running():
                self.error_occurred.emit(f"Unexpected error: {e}")
        finally:
            with QMutexLocker(self._busy_lock):
                self._is_busy = False

    @Slot()
    def request_stop(self):
        with QMutexLocker(self._is_running_lock):
            self._is_actually_running = False


###############################################################################
# CT400ControlPanel
###############################################################################
class CT400ControlPanel(QWidget):
    scan_data_ready = QtCore.Signal(np.ndarray, np.ndarray, float)
    progress_updated = QtCore.Signal(int)

    def __init__(
        self,
        shared_settings: ScanSettings,
        ct400_device: AbstractCT400 | None,
        config: AppConfig,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.shared_settings = shared_settings
        self.ct400 = ct400_device
        self.config = config
        self.is_instrument_connected = self.ct400 is not None
        self.scanning = False
        self.scan_worker: ScanWorker | None = None
        self.scan_thread: QThread | None = None
        self.setObjectName(ID_CT400_SCAN_PANEL)
        try:
            self.setStyleSheet(CT400_CONTROL_PANEL_STYLE)
        except NameError:
            logger.warning("CT400_CONTROL_PANEL_STYLE not found.")

        self._init_ui()
        # ... rest of __init__ is the same
        self.laser_power.setValidator(QtGui.QDoubleValidator(0.1, 50.0, 3))
        self.initial_wl.setValidator(QtGui.QDoubleValidator(1440.0, 1640.0, 3))
        self.final_wl.setValidator(QtGui.QDoubleValidator(1440.0, 1640.0, 3))
        self.resolution.setValidator(QtGui.QIntValidator(1, 1000))
        self.motor_speed.setValidator(QtGui.QIntValidator(1, 100))
        self._connect_settings_signals()
        self.on_instrument_connected(self.is_instrument_connected)
        self.progress_updated.connect(self.update_progress_bar)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        scan_config_group = QGroupBox("Scan Configuration")
        scan_config_layout = QGridLayout()
        scan_config_layout.setSpacing(8)

        current_row = 0
        scan_config_layout.addWidget(QLabel("Start λ (nm):"), current_row, 0)
        self.initial_wl = QLineEdit()
        scan_config_layout.addWidget(self.initial_wl, current_row, 1)

        current_row += 1
        scan_config_layout.addWidget(QLabel("End λ (nm):"), current_row, 0)
        self.final_wl = QLineEdit()
        scan_config_layout.addWidget(self.final_wl, current_row, 1)

        current_row += 1
        scan_config_layout.addWidget(QLabel("Resolution (pm):"), current_row, 0)
        self.resolution = QLineEdit()
        scan_config_layout.addWidget(self.resolution, current_row, 1)

        current_row += 1
        scan_config_layout.addWidget(QLabel("Speed (nm/s):"), current_row, 0)
        self.motor_speed = QLineEdit()
        scan_config_layout.addWidget(self.motor_speed, current_row, 1)

        current_row += 1
        scan_config_layout.addWidget(QLabel("Laser Power:"), current_row, 0)
        power_layout = QHBoxLayout()
        self.laser_power = QLineEdit()
        power_layout.addWidget(self.laser_power)
        self.power_unit = QComboBox()
        self.power_unit.addItems(["mW", "dBm"])
        power_layout.addWidget(self.power_unit)
        scan_config_layout.addLayout(power_layout, current_row, 1)

        current_row += 1
        scan_config_layout.addWidget(QLabel("Laser Input Port:"), current_row, 0)
        self.input_port = QComboBox()
        for member in LaserInput:
            display_text = member.name.split("_")[-1]
            self.input_port.addItem(display_text, userData=member)
        scan_config_layout.addWidget(self.input_port, current_row, 1)

        scan_config_layout.setColumnStretch(0, 0)
        scan_config_layout.setColumnStretch(1, 1)
        scan_config_group.setLayout(scan_config_layout)
        main_layout.addWidget(scan_config_group)

        control_group = QGroupBox("Operation")
        control_layout = QVBoxLayout()
        self.scan_btn = QPushButton("Start Scan")
        self.scan_btn.setObjectName(ID_SCAN_BUTTON)
        self.scan_btn.setIcon(QtGui.QIcon(":/icons/play.svg"))
        self.scan_btn.setMinimumHeight(35)
        self.scan_btn.setMinimumWidth(130)
        control_layout.addWidget(self.scan_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        control_layout.addWidget(self.progress_bar)
        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)

        main_layout.addStretch(1)
        self.scan_btn.clicked.connect(self._toggle_scan)

    def _connect_settings_signals(self):
        self.resolution.textChanged.connect(self.update_shared_settings)
        self.motor_speed.textChanged.connect(self.update_shared_settings)
        self.laser_power.textChanged.connect(self.update_shared_settings)
        self.power_unit.currentIndexChanged.connect(self.update_shared_settings)
        QTimer.singleShot(0, self.update_shared_settings)

    def on_instrument_connected(self, is_connected: bool):
        logger.info(f"Scan Panel notified: Instrument connected = {is_connected}")
        self.is_instrument_connected = is_connected
        enable_config_inputs = is_connected and not self.scanning
        config_widgets = [
            self.initial_wl,
            self.final_wl,
            self.resolution,
            self.motor_speed,
            self.laser_power,
            self.power_unit,
            self.input_port,
        ]
        for widget in config_widgets:
            widget.setEnabled(enable_config_inputs)

        self.scan_btn.setEnabled(is_connected)
        if not is_connected and self.scanning:
            logger.warning("Instrument disconnected during scan. Forcing stop.")
            self._stop_scan(cancelled=True)

    def set_instrument(self, ct400_device: AbstractCT400):
        """Assigns the live CT400 device to the panel after lazy initialization."""
        logger.info(f"'{self.objectName()}' received live instrument object.")
        self.ct400 = ct400_device
        # Also update the worker inside HistogramControlPanel
        if isinstance(self, HistogramControlPanel) and hasattr(self, "power_fetch_worker"):
            self.power_fetch_worker.ct400 = ct400_device
        self.on_instrument_connected(self.ct400 is not None)

    @Slot()
    def _toggle_scan(self):
        if not self.scanning:
            self._start_scan()
        else:
            self._stop_scan(cancelled=True)

    def update_shared_settings(self):
        try:
            self.shared_settings.resolution = self.resolution.text()
            self.shared_settings.motor_speed = self.motor_speed.text()
            self.shared_settings.laser_power = self.laser_power.text()
            self.shared_settings.power_unit = self.power_unit.currentText()
        except Exception as e:
            logger.warning(f"Could not update shared settings: {e}")

    def _get_laser_power_mw(self) -> float:
        value = float(self.laser_power.text())
        if self.power_unit.currentText() == "dBm":
            return 1.0 * (10 ** (value / 10.0))
        return value

    def _start_scan(self):
        if not self.is_instrument_connected:
            QMessageBox.warning(self, "Not Connected", "CT400 device is not connected.")
            return
        if self.scanning:
            return
        try:
            start_wl = float(self.initial_wl.text())
            end_wl = float(self.final_wl.text())
            resolution = int(self.resolution.text())
            laser_power_mw = self._get_laser_power_mw()
            input_port_enum = self.input_port.currentData()
            if not isinstance(input_port_enum, LaserInput):
                logger.error(f"Invalid data type for input port: {type(input_port_enum)}. Expected LaserInput.")
                QMessageBox.critical(self, "Internal Error", "Invalid laser input port configuration.")
                self._reset_scan_ui()
                return
            if start_wl >= end_wl or resolution <= 0:
                raise ValueError("Invalid scan parameters.")

            self.scanning = True
            self.scan_btn.setText("Stop Scan")
            self.scan_btn.setIcon(QtGui.QIcon(":/icons/stop.svg"))
            self.scan_btn.setProperty(PROP_SCANNING, True)
            self.scan_btn.style().unpolish(self.scan_btn)
            self.scan_btn.style().polish(self.scan_btn)
            self.on_instrument_connected(True)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)

            # --- REFACTORED THREADING LOGIC ---
            self.scan_thread = QThread(self)
            self.scan_worker = ScanWorker(
                self.ct400,
                start_wl,
                end_wl,
                resolution,
                laser_power_mw,
                input_port_enum,
                disable_wl=self.config.scan_defaults.safe_parking_wavelength,
                disable_power=self.config.scan_defaults.laser_power,
            )
            self.scan_worker.moveToThread(self.scan_thread)

            # Connect signals
            self.scan_thread.started.connect(self.scan_worker.do_scan)
            self.scan_worker.progress_signal.connect(self.progress_updated)
            self.scan_worker.completed_signal.connect(self._handle_scan_completed)
            self.scan_worker.error_signal.connect(self._handle_scan_error)

            # When worker finishes, it tells the thread to quit
            self.scan_worker.finished.connect(self.scan_thread.quit)
            # When thread finishes, clean up both thread and worker
            self.scan_thread.finished.connect(self._scan_thread_finished)

            self.scan_thread.start()
            # --- END REFACTORED THREADING LOGIC ---
            logger.info("Scan worker thread started.")
        except ValueError as ve:
            QMessageBox.critical(self, "Invalid Input", f"Invalid scan parameter: {ve}")
            logger.warning(f"Scan start validation failed: {ve}")
        except Exception as e:
            QMessageBox.critical(self, "Scan Start Error", f"Could not start scan: {e}")
            logger.error(f"Error starting scan: {e}", exc_info=True)
            self._reset_scan_ui()

    def _stop_scan(self, cancelled=False):
        logger.info(f"Stopping scan (Cancelled: {cancelled}).")
        # Check both the worker and thread exist
        if self.scan_worker and self.scan_thread and self.scan_thread.isRunning():
            self.scan_worker.stop()
            # The worker will finish its current loop, run the finally block,
            # and emit finished(), which will quit the thread.
        if cancelled:
            self._reset_scan_ui(status_msg="Scan cancelled by user.")

    @Slot(int)
    def update_progress_bar(self, value: int):
        if self.progress_bar.isVisible():
            self.progress_bar.setValue(value)

    @Slot(np.ndarray, np.ndarray, float)
    def _handle_scan_completed(self, wavelengths: np.ndarray, powers_scan_data: np.ndarray, final_pout: float):
        logger.info("ScanPanel: Scan completed signal received.")
        plotting_power_data = np.array([])
        try:
            if hasattr(powers_scan_data, "shape") and powers_scan_data.shape[0] > 0:
                plotting_power_data = powers_scan_data[0]
        except Exception as e:
            logger.error(f"Error extracting plotting data: {e}")
        self.scan_data_ready.emit(wavelengths, plotting_power_data, final_pout)

    @Slot(str)
    def _handle_scan_error(self, error_msg: str):
        logger.error(f"ScanPanel: Scan error signal: {error_msg}")
        QMessageBox.critical(self, "Scan Error", f"Scan error:\n{error_msg}")

    # This slot is connected to the thread's finished signal
    @Slot()
    def _scan_thread_finished(self):
        logger.info("ScanPanel: Scan thread finished.")
        status_msg = "Scan finished."
        # Check if the worker still exists and was stopped manually
        if self.scan_worker and not self.scan_worker._running and self.scanning:
            status_msg = "Scan cancelled."

        # Now that the thread is finished, we can safely delete the worker and thread objects.
        if self.scan_worker:
            self.scan_worker.deleteLater()
            self.scan_worker = None
        if self.scan_thread:
            self.scan_thread.deleteLater()
            self.scan_thread = None

        self._reset_scan_ui(status_msg=status_msg)

    def _reset_scan_ui(self, status_msg: str = "Ready"):
        self.scanning = False
        self.scan_btn.setText("Start Scan")
        self.scan_btn.setIcon(QtGui.QIcon(":/icons/play.svg"))
        self.scan_btn.setProperty(PROP_SCANNING, False)
        self.scan_btn.style().unpolish(self.scan_btn)
        self.scan_btn.style().polish(self.scan_btn)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.on_instrument_connected(self.is_instrument_connected)
        logger.info(f"Scan panel UI reset. Status: {status_msg}")


###############################################################################
# HistogramControlPanel
###############################################################################
class HistogramControlPanel(QWidget):
    _THREAD_WAIT_TIMEOUT_MS = 2000
    power_data_ready = QtCore.Signal(dict)

    def __init__(
        self,
        ct400_device: AbstractCT400 | None,
        config: AppConfig,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.ct400 = ct400_device
        self.config = config
        self.is_instrument_connected = self.ct400 is not None
        self.monitoring = False

        self.power_fetch_thread = QThread(self)
        self.power_fetch_worker = PowerFetchWorker(self.ct400)
        self.power_fetch_worker.moveToThread(self.power_fetch_thread)

        self.power_fetch_worker.data_ready.connect(self._handle_worker_data_ready)
        self.power_fetch_worker.error_occurred.connect(self._handle_worker_error)
        self.power_fetch_thread.started.connect(lambda: logger.info("Power fetch worker thread started."))
        self.power_fetch_thread.finished.connect(self.power_fetch_worker.deleteLater)
        self.power_fetch_thread.finished.connect(self.power_fetch_thread.deleteLater)
        self.power_fetch_thread.finished.connect(
            lambda: logger.info("Power fetch worker thread finished and cleaned up.")
        )

        self.timer = QTimer(self)
        self.timer.setInterval(MONITOR_TIMER_INTERVAL_MS)
        self.timer.timeout.connect(self._request_power_fetch_from_worker)
        self.setObjectName(ID_CT400_MONITOR_PANEL)

        try:
            self.setStyleSheet(CT400_CONTROL_PANEL_STYLE)
        except NameError:
            logger.warning("CT400_CONTROL_PANEL_STYLE not found.")
        self._init_ui()
        self.laser_power.setValidator(QtGui.QDoubleValidator(0.1, 50.0, 3))
        self.wavelength_input.setValidator(QtGui.QDoubleValidator(1440.0, 1640.0, 3))

        self.on_instrument_connected(self.is_instrument_connected)
        self.power_fetch_thread.start()

    def _init_ui(self):
        panel_layout = QGridLayout(self)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(10)

        config_group = QGroupBox("Measurement Configuration")
        config_internal_layout = QGridLayout()
        config_internal_layout.setSpacing(8)

        config_internal_layout.addWidget(QLabel("Wavelength (nm):"), 0, 0)
        self.wavelength_input = QLineEdit()
        config_internal_layout.addWidget(self.wavelength_input, 0, 1)

        config_internal_layout.addWidget(QLabel("Laser Power:"), 1, 0)
        power_layout = QHBoxLayout()
        self.laser_power = QLineEdit()
        power_layout.addWidget(self.laser_power)
        self.power_unit = QComboBox()
        self.power_unit.addItems(["mW", "dBm"])
        power_layout.addWidget(self.power_unit)
        config_internal_layout.addLayout(power_layout, 1, 1)

        config_internal_layout.addWidget(QLabel("Laser Input Port:"), 2, 0)
        self.input_port = QComboBox()
        for member in LaserInput:
            display_text = member.name.split("_")[-1]
            self.input_port.addItem(display_text, userData=member)
        config_internal_layout.addWidget(self.input_port, 2, 1)

        config_internal_layout.setColumnStretch(1, 1)
        config_group.setLayout(config_internal_layout)
        panel_layout.addWidget(config_group, 0, 0, 1, 2)

        detector_group = QGroupBox("Active Detectors")
        detector_checkbox_layout = QGridLayout()
        detector_checkbox_layout.setSpacing(8)
        self.detector_cbs: list[QCheckBox] = []
        for i in range(4):
            cb = QCheckBox(f"Det {i + 1}")
            cb.setChecked(True)
            self.detector_cbs.append(cb)
            row, col = i // 2, i % 2
            detector_checkbox_layout.addWidget(cb, row, col)
        detector_group.setLayout(detector_checkbox_layout)
        panel_layout.addWidget(detector_group, 1, 0)

        operation_group = QGroupBox("Operation")
        operation_button_wrapper_layout = QVBoxLayout()
        self.monitor_btn = QPushButton("Start Monitoring")
        self.monitor_btn.setObjectName(ID_MONITOR_BUTTON)
        self.monitor_btn.setIcon(QtGui.QIcon(":/icons/play.svg"))
        self.monitor_btn.setMinimumHeight(100)
        self.monitor_btn.setMinimumWidth(150)
        operation_button_wrapper_layout.addWidget(self.monitor_btn)
        operation_group.setLayout(operation_button_wrapper_layout)
        panel_layout.addWidget(operation_group, 1, 1, Qt.AlignmentFlag.AlignTop)

        panel_layout.setRowStretch(2, 1)

        self.monitor_btn.clicked.connect(self._toggle_monitoring)
        for cb in self.detector_cbs:
            cb.stateChanged.connect(self._detector_selection_changed)

    def set_instrument(self, ct400_device: AbstractCT400):
        """Assigns the live CT400 device to the panel after lazy initialization."""
        logger.info(f"'{self.objectName()}' received live instrument object.")
        self.ct400 = ct400_device
        # Also update the worker inside HistogramControlPanel
        if isinstance(self, HistogramControlPanel) and hasattr(self, "power_fetch_worker"):
            self.power_fetch_worker.ct400 = ct400_device
        self.on_instrument_connected(self.ct400 is not None)

    def on_instrument_connected(self, is_connected: bool):
        logger.info(f"Monitor Panel: Instrument connected = {is_connected}")
        self.is_instrument_connected = is_connected
        if hasattr(self, "power_fetch_worker"):
            self.power_fetch_worker.ct400 = self.ct400 if is_connected else None
        enable_config_and_detectors = is_connected and not self.monitoring
        config_widgets = [
            self.wavelength_input,
            self.laser_power,
            self.power_unit,
            self.input_port,
        ]
        for widget in config_widgets:
            widget.setEnabled(enable_config_and_detectors)

        for cb in self.detector_cbs:
            cb.setEnabled(is_connected)

        self.monitor_btn.setEnabled(is_connected)
        if not is_connected and self.monitoring:
            logger.warning("Monitor Panel: Instrument disconnected during monitoring. Stopping.")
            self._stop_monitoring(instrument_error_or_disconnect=True)

    def _get_laser_power_mw(self) -> float:
        value = float(self.laser_power.text())
        if self.power_unit.currentText() == "dBm":
            return 1.0 * (10 ** (value / 10.0))
        return value

    @Slot()
    def _toggle_monitoring(self):
        if not self.monitoring:
            self._start_monitoring()
        else:
            self._stop_monitoring()

    def _apply_monitoring_settings(self) -> bool:
        if not self.is_instrument_connected:
            logger.error("Monitor Panel: Cannot apply settings, instrument not connected.")
            return False
        try:
            wavelength = float(self.wavelength_input.text())
            power_mw = self._get_laser_power_mw()
            input_port_enum = self.input_port.currentData()
            if not isinstance(input_port_enum, LaserInput):
                logger.error(f"Invalid data type for input port: {type(input_port_enum)}. Expected LaserInput.")
                QMessageBox.critical(self, "Internal Error", "Invalid laser input port configuration.")
                return False

            det_enables = [Enable.ENABLE if cb.isChecked() else Enable.DISABLE for cb in self.detector_cbs]
            if len(det_enables) == 4:
                self.ct400.set_detector_array(det_enables[1], det_enables[2], det_enables[3], Enable.DISABLE)
            else:
                logger.error(f"Monitor Panel: Incorrect number of detector checkboxes ({len(det_enables)}).")
                return False

            self.ct400.cmd_laser(input_port_enum, Enable.ENABLE, wavelength, power_mw)
            logger.info(
                f"Monitor Panel: Laser commanded for monitoring. Port {input_port_enum.value}, WL {wavelength}nm, P {power_mw:.3f}mW"
            )
            return True
        except ValueError as ve:
            QMessageBox.critical(self, "Invalid Input", f"Invalid monitoring parameter: {ve}")
            logger.warning(f"Monitor Panel: Settings validation failed: {ve}")
            return False
        except CT400Error as ce:
            QMessageBox.critical(self, "Instrument Error", f"Failed to apply settings: {ce}")
            logger.error(f"Monitor Panel: CT400 Error applying settings: {ce}")
            return False
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Unexpected error applying settings: {e}")
            logger.error(f"Monitor Panel: Error applying settings: {e}", exc_info=True)
            return False

    def _start_monitoring(self):
        if not self.is_instrument_connected:
            QMessageBox.warning(self, "Not Connected", "CT400 device is not connected.")
            return
        if self.monitoring:
            return
        if not self._apply_monitoring_settings():
            return

        logger.info("Monitor Panel: Starting power monitoring.")
        self.monitoring = True
        with QMutexLocker(self.power_fetch_worker._is_running_lock):
            self.power_fetch_worker._is_actually_running = True

        self.monitor_btn.setText("Stop Monitoring")
        self.monitor_btn.setIcon(QtGui.QIcon(":/icons/stop-circle.svg"))
        self.monitor_btn.setProperty(PROP_MONITORING, True)
        self.monitor_btn.style().unpolish(self.monitor_btn)
        self.monitor_btn.style().polish(self.monitor_btn)
        self.on_instrument_connected(True)
        self.timer.start()
        logger.debug(f"Monitor Panel: Timer started (Interval: {self.timer.interval()}ms) to trigger worker.")

    def _stop_monitoring(self, instrument_error_or_disconnect=False):
        if not self.monitoring and not instrument_error_or_disconnect:
            return

        logger.info(
            f"Monitor Panel: Stopping power monitoring. Forced by error/disconnect: {instrument_error_or_disconnect}"
        )
        was_actively_monitoring = self.monitoring
        self.monitoring = False
        if self.timer.isActive():
            logger.debug("Monitor Panel: Stopping QTimer.")
            self.timer.stop()
        if hasattr(self, "power_fetch_worker"):
            QMetaObject.invokeMethod(
                self.power_fetch_worker,
                "request_stop",
                Qt.ConnectionType.QueuedConnection,
            )

        if was_actively_monitoring and not instrument_error_or_disconnect:
            self._perform_laser_disable()

        self.monitor_btn.setText("Start Monitoring")
        self.monitor_btn.setIcon(QtGui.QIcon(":/icons/play.svg"))
        self.monitor_btn.setProperty(PROP_MONITORING, False)
        self.monitor_btn.style().unpolish(self.monitor_btn)
        self.monitor_btn.style().polish(self.monitor_btn)
        self.on_instrument_connected(self.is_instrument_connected)
        logger.debug("Monitor Panel: UI reset and stop requested.")

    def _perform_laser_disable(self):
        if self.is_instrument_connected and self.ct400:
            try:
                logger.info("Monitor Panel: Disabling laser via _perform_laser_disable.")
                input_port_data = self.input_port.currentData()
                if not isinstance(input_port_data, LaserInput):
                    logger.warning(
                        f"Monitor Panel: input_port.currentData() was not LaserInput type ({type(input_port_data)}). Falling back to currentText."
                    )
                    input_port_str = self.input_port.currentText()
                    input_port_enum = LaserInput(int(input_port_str))
                else:
                    input_port_enum = input_port_data

                default_wl_disable = 1550.0
                default_power_disable = 1.0
                self.ct400.cmd_laser(
                    input_port_enum,
                    Enable.DISABLE,
                    float(default_wl_disable),
                    float(default_power_disable),
                )
                logger.info(f"Monitor Panel: Laser disable command sent for port {input_port_enum.name}.")
            except (CT400Error, ValueError) as e:
                logger.error(f"Monitor Panel: Failed to disable laser: {e}")
            except Exception as e_unexp:
                logger.error(
                    f"Monitor Panel: Unexpected error disabling laser: {e_unexp}",
                    exc_info=True,
                )
        else:
            logger.warning(
                "Monitor Panel: Cannot disable laser, CT400 not connected/available or instrument flag is false."
            )

    @Slot()
    def _request_power_fetch_from_worker(self):
        if not self.monitoring:
            return
        if hasattr(self, "power_fetch_worker") and self.power_fetch_worker is not None:
            if not self.power_fetch_worker.is_worker_running():
                return
            QMetaObject.invokeMethod(
                self.power_fetch_worker,
                "fetch_power",
                Qt.ConnectionType.QueuedConnection,
            )

    @Slot(object)
    def _handle_worker_data_ready(self, power_data_tuple: PowerData):
        if not self.monitoring:
            logger.debug("Monitor Panel: Received worker data but no longer monitoring. Discarding.")
            return

        logger.debug(
            f"Monitor Panel (Main Thread): Received power data from worker: Pout={power_data_tuple.pout}, Detectors={power_data_tuple.detectors}"
        )
        try:
            detector_string_map = {
                Detector.DE_1: "Det 1",
                Detector.DE_2: "Det 2",
                Detector.DE_3: "Det 3",
                Detector.DE_4: "Det 4",
            }
            mapped_detector_values = {}
            if power_data_tuple.detectors:
                for i, enum_key in enumerate(detector_string_map.keys()):
                    if enum_key in power_data_tuple.detectors and i < len(self.detector_cbs):
                        str_key = detector_string_map[enum_key]
                        is_checked = self.detector_cbs[i].isChecked()
                        if is_checked:
                            mapped_detector_values[str_key] = power_data_tuple.detectors[enum_key]
                        else:
                            mapped_detector_values[str_key] = 0.0
                    elif enum_key not in power_data_tuple.detectors:
                        str_key = detector_string_map.get(enum_key)
                        if str_key:
                            mapped_detector_values[str_key] = 0.0
                            logger.debug(f"Monitor Panel: No data from CT400 for {str_key}, setting to 0.")

            emit_data = {
                "pout": power_data_tuple.pout,
                "detectors": mapped_detector_values,
            }
            logger.debug(f"Monitor Panel (Main Thread): Emitting processed power data for UI: {emit_data}")
            self.power_data_ready.emit(emit_data)
        except Exception as e:
            logger.exception(f"Monitor Panel (Main Thread): Error processing worker data: {e}")

    @Slot(str)
    def _handle_worker_error(self, error_msg: str):
        logger.error(f"Monitor Panel (Main Thread): Error from power fetch worker: {error_msg}")
        if self.monitoring:
            self._stop_monitoring(instrument_error_or_disconnect=True)
            QMessageBox.critical(
                self,
                "Monitoring Error",
                f"Failed to read power: {error_msg}\nMonitoring stopped.",
            )

    def cleanup_worker_thread(self):
        logger.info("HistogramControlPanel: Initiating cleanup of power fetch worker thread.")
        if self.monitoring:
            logger.info("HistogramControlPanel.cleanup: Monitoring was active, stopping it first.")
            self._stop_monitoring()
        if hasattr(self, "power_fetch_thread") and self.power_fetch_thread.isRunning():
            logger.info("HistogramControlPanel.cleanup: Quitting power fetch thread.")
            self.power_fetch_thread.quit()
            if not self.power_fetch_thread.wait(self._THREAD_WAIT_TIMEOUT_MS):
                logger.warning("Power fetch worker thread did not quit gracefully during cleanup. Terminating.")
                self.power_fetch_thread.terminate()
                self.power_fetch_thread.wait()
            else:
                logger.info("Power fetch worker thread quit gracefully during cleanup.")
        elif hasattr(self, "power_fetch_thread"):
            logger.info("Power fetch worker thread was not running at cleanup.")

    @Slot()
    def _detector_selection_changed(self):
        if self.is_instrument_connected and self.ct400:
            logger.info("Monitor Panel: Detector selection changed, re-applying to CT400.")
            try:
                det_enables = [Enable.ENABLE if cb.isChecked() else Enable.DISABLE for cb in self.detector_cbs]
                if len(det_enables) == 4:
                    self.ct400.set_detector_array(det_enables[1], det_enables[2], det_enables[3], Enable.DISABLE)
                else:
                    logger.error(f"Monitor Panel: Incorrect detector checkbox count ({len(det_enables)}).")
            except CT400Error as e:
                logger.error(f"Monitor Panel: Failed to update CT400 detector array: {e}")
                QMessageBox.warning(
                    self,
                    "Detector Error",
                    f"Failed to update detector selection on CT400: {e}",
                )
            except Exception as e:
                logger.exception(f"Monitor Panel: Unexpected error updating CT400 detectors: {e}")
                QMessageBox.warning(self, "Detector Error", f"Error updating detectors on CT400: {e}")

    def closeEvent(self, event: QtGui.QCloseEvent):
        """Handle widget close event to clean up the worker thread."""
        self.cleanup_worker_thread()
        super().closeEvent(event)
