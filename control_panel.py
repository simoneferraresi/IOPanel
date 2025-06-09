import logging
from ctypes import create_string_buffer
from typing import Any, Dict, List, Optional

import numpy as np
from PySide6 import QtCore, QtGui
from PySide6.QtCore import Qt, QTimer, Slot
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

# Import CT400 types
try:
    from CT400_updated import (
        CT400,
        CT400Error,
        Detector,
        Enable,
        LaserInput,
        LaserSource,
        PowerData,
        Unit,
    )
except ImportError:
    logging.getLogger("LabApp.control_panel").warning(
        "CT400_updated module not found, defining dummy types."
    )

    class CT400:
        pass

    class Detector:
        DE_1 = 1

    class Enable:
        ENABLE = 1
        DISABLE = 0

    class LaserInput:
        LI_1 = 1

    class LaserSource:
        LS_TunicsT100s_HP = 3

    class Unit:
        Unit_mW = 0

    class CT400Error(Exception):
        pass

    class PowerData:  # Dummy for type hint
        def __init__(self, pout, detectors):
            self.pout = pout
            self.detectors = detectors


from styles import CT400_CONTROL_PANEL_STYLE

MONITOR_TIMER_INTERVAL_MS = 100  # Increased from 50ms for less load, can be tuned
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
class ScanWorker(QtCore.QThread):
    completed_signal = QtCore.Signal(object, object, object)
    progress_signal = QtCore.Signal(int)
    error_signal = QtCore.Signal(str)

    def __init__(
        self,
        ct400: CT400,
        start_wl: float,
        end_wl: float,
        resolution: int,
        laser_power: float,
        input_port: LaserInput,
        disable_wl: float = 1550.0,
        disable_power: float = 1.0,
        parent: Optional[QtCore.QObject] = None,
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

    def run(self):
        error_buf = create_string_buffer(1024)
        scan_started = False
        try:
            logger.info(
                f"ScanWorker: {self.start_wl}nm to {self.end_wl}nm, Res: {self.resolution}pm"
            )
            self.ct400.set_scan(self.laser_power, self.start_wl, self.end_wl)
            self.ct400.set_sampling_res(self.resolution)
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
                    logger.error(
                        f"ScanWorker: Scan error (ScanWaitEnd): {error_msg} (Code: {error_status})"
                    )
                    raise CT400Error(f"Scan failed: {error_msg}")
                self.msleep(100)

            if not self._running:
                logger.info("ScanWorker: Scan cancelled by user.")
                raise CT400Error("Scan cancelled by user.")

            logger.info("ScanWorker: Retrieving data points...")
            detectors_to_get = [Detector.DE_1]
            wavelengths, powers_scan_data = self.ct400.get_data_points(detectors_to_get)
            logger.info(
                f"ScanWorker: Data retrieved. WL: {len(wavelengths)}, Power Shape: {powers_scan_data.shape}"
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
                    logger.info("ScanWorker: Ensuring scan is stopped...")
                    self.ct400.stop_scan()
                logger.info("ScanWorker: Disabling laser...")
                self.ct400.cmd_laser(
                    laser_input=self.input_port,
                    enable=Enable.DISABLE,
                    wavelength=float(self.disable_wl),
                    power=float(self.disable_power),
                )
            except Exception as e:  # Catch broadly during cleanup
                logger.error(f"ScanWorker: Error during cleanup: {e}")
            logger.info("ScanWorker: Finished.")

    def stop(self):
        logger.info("ScanWorker: Stop requested.")
        self._running = False


###############################################################################
# CT400ControlPanel
###############################################################################
class CT400ControlPanel(QWidget):
    scan_data_ready = QtCore.Signal(object, object, object)
    progress_updated = QtCore.Signal(int)

    def __init__(
        self,
        shared_settings: ScanSettings,
        ct400_device: Optional[CT400],
        config: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.shared_settings = shared_settings
        self.ct400 = ct400_device
        self.config = config
        self.is_instrument_connected = self.ct400 is not None
        self.scanning = False
        self.scan_worker: Optional[ScanWorker] = None
        self.setObjectName("ct400ScanPanel")
        try:
            self.setStyleSheet(CT400_CONTROL_PANEL_STYLE)
        except NameError:
            logger.warning("CT400_CONTROL_PANEL_STYLE not found.")
        self._init_ui()
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
        self.input_port.addItems(["1", "2", "3", "4"])
        scan_config_layout.addWidget(self.input_port, current_row, 1)
        scan_config_layout.setColumnStretch(0, 0)
        scan_config_layout.setColumnStretch(1, 1)
        scan_config_group.setLayout(scan_config_layout)
        main_layout.addWidget(scan_config_group)
        control_group = QGroupBox("Operation")
        control_layout = QVBoxLayout()
        self.scan_btn = QPushButton("Start Scan")
        self.scan_btn.setIcon(QtGui.QIcon(":/icons/play.svg"))
        self.scan_btn.setMinimumHeight(35)
        self.scan_btn.setMinimumWidth(130)
        # Initial style: Green for "Start Scan"
        self.scan_btn.setStyleSheet(
            "QPushButton {"
            "   background-color: #007200;"  # Green
            "   color: white;"
            "   font-weight: bold;"
            "   font-size: 15;"
            "   border: none;"
            "   padding: 8px 12px;"  # Adjust padding as needed
            "   border-radius: 3px;"  # Slightly rounded corners
            "}"
            "QPushButton:hover { background-color: #006400; }"  # Darker green on hover
            "QPushButton:pressed { background-color: #004b23; }"  # Even darker on press
            "QPushButton:disabled { background-color: #e0e0e0; color: #bdbdbd; }"  # Disabled style
        )
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

        # Configuration inputs are disabled during scanning
        enable_config_inputs = (
            is_connected and not self.scanning
        )  # Use self.scanning here
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

        # Scan button is enabled if instrument is connected. Its text/role changes based on scanning state.
        self.scan_btn.setEnabled(is_connected)

        if not is_connected and self.scanning:  # Use self.scanning here
            logger.warning("Instrument disconnected during scan. Forcing stop.")
            self._stop_scan(cancelled=True)

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
        if not self.is_instrument_connected or self.ct400 is None:
            QMessageBox.warning(self, "Not Connected", "CT400 device is not connected.")
            return
        if self.scanning:
            return
        try:
            start_wl = float(self.initial_wl.text())
            end_wl = float(self.final_wl.text())
            resolution = int(self.resolution.text())
            laser_power_mw = self._get_laser_power_mw()
            # speed = int(self.motor_speed.text()) # Speed not directly used by worker CT400.set_scan
            input_port_enum = LaserInput(int(self.input_port.currentText()))
            if start_wl >= end_wl or resolution <= 0:
                raise ValueError("Invalid scan parameters.")

            self.scanning = True
            self.scan_btn.setText("Stop Scan")
            self.scan_btn.setIcon(QtGui.QIcon(":/icons/stop.svg"))
            self.scan_btn.setStyleSheet(
                "QPushButton {"
                "   background-color: #85182a;"  # Red
                "   color: white;"
                "   font-weight: bold;"
                "   border: none;"
                "   padding: 8px 12px;"
                "   border-radius: 3px;"
                "}"
                "QPushButton:hover { background-color: #6e1423; }"
                "QPushButton:pressed { background-color: #641220; }"
                # Disabled state will be handled by the button's enabled state
            )
            self.on_instrument_connected(True)  # Updates config input states
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)

            self.scan_worker = ScanWorker(
                self.ct400,
                start_wl,
                end_wl,
                resolution,
                laser_power_mw,
                input_port_enum,
            )
            self.scan_worker.progress_signal.connect(self.progress_updated.emit)
            self.scan_worker.completed_signal.connect(self._handle_scan_completed)
            self.scan_worker.error_signal.connect(self._handle_scan_error)
            self.scan_worker.finished.connect(self._scan_worker_finished)
            self.scan_worker.start()
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
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.stop()
        # UI reset will be handled by _scan_worker_finished or if cancelled, more immediately here
        if cancelled:
            self._reset_scan_ui(status_msg="Scan cancelled by user.")

    @Slot(int)
    def update_progress_bar(self, value: int):
        if self.progress_bar.isVisible():
            self.progress_bar.setValue(value)

    @Slot(object, object, object)
    def _handle_scan_completed(self, wavelengths, powers_scan_data, final_pout):
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
        # UI reset handled by _scan_worker_finished

    @Slot()
    def _scan_worker_finished(self):
        logger.info("ScanPanel: Scan worker finished.")
        # Determine appropriate status message
        status_msg = "Scan finished."
        if (
            self.scan_worker
            and hasattr(self.scan_worker, "_running")
            and not self.scan_worker._running
            and self.scanning
        ):  # Check if it was scanning and user requested stop
            status_msg = "Scan cancelled."

        self._reset_scan_ui(status_msg=status_msg)
        self.scan_worker = None

    def _reset_scan_ui(self, status_msg: str = "Ready"):
        self.scanning = False
        self.scan_btn.setText("Start Scan")
        self.scan_btn.setIcon(QtGui.QIcon(":/icons/play.svg"))
        self.scan_btn.setStyleSheet(
            "QPushButton {"
            "   background-color: #007200;"  # Green
            "   color: white;"
            "   font-weight: bold;"
            "   font-size: 15;"
            "   border: none;"
            "   padding: 8px 12px;"  # Adjust padding as needed
            "   border-radius: 3px;"  # Slightly rounded corners
            "}"
            "QPushButton:hover { background-color: #006400; }"  # Darker green on hover
            "QPushButton:pressed { background-color: #004b23; }"  # Even darker on press
            "QPushButton:disabled { background-color: #e0e0e0; color: #bdbdbd; }"  # Disabled style
        )
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.on_instrument_connected(
            self.is_instrument_connected
        )  # Re-evaluate input enable states
        logger.info(f"Scan panel UI reset. Status: {status_msg}")


###############################################################################
# HistogramControlPanel
###############################################################################
class HistogramControlPanel(QWidget):
    power_data_ready = QtCore.Signal(dict)

    def __init__(
        self,
        ct400_device: Optional[CT400],
        config: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.ct400 = ct400_device
        self.config = config
        self.is_instrument_connected = self.ct400 is not None
        self.monitoring = False
        self.timer = QTimer(self)
        self.timer.setInterval(MONITOR_TIMER_INTERVAL_MS)
        self.timer.timeout.connect(self._fetch_power_update)
        self.setObjectName("ct400MonitorPanel")
        try:
            self.setStyleSheet(CT400_CONTROL_PANEL_STYLE)
        except NameError:
            logger.warning("CT400_CONTROL_PANEL_STYLE not found.")
        self._init_ui()
        self.laser_power.setValidator(QtGui.QDoubleValidator(0.1, 50.0, 3))
        self.wavelength_input.setValidator(QtGui.QDoubleValidator(1440.0, 1640.0, 3))
        self.on_instrument_connected(self.is_instrument_connected)

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
        self.input_port.addItems(["1", "2", "3", "4"])
        config_internal_layout.addWidget(self.input_port, 2, 1)
        config_internal_layout.setColumnStretch(1, 1)
        config_group.setLayout(config_internal_layout)
        panel_layout.addWidget(config_group, 0, 0, 1, 2)
        detector_group = QGroupBox("Active Detectors")
        detector_checkbox_layout = QGridLayout()
        detector_checkbox_layout.setSpacing(8)
        self.detector_cbs: List[QCheckBox] = []
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
        # Initial style: Blue for "Start Monitoring"
        self.monitor_btn.setStyleSheet(
            "QPushButton {"
            "   background-color: #0077b6;"  # Blue
            "   color: white;"
            "   font-weight: bold;"
            "   border: none;"
            "   padding: 10px 15px;"  # Adjust padding as needed
            "   border-radius: 3px;"
            "}"
            "QPushButton:hover { background-color: #023e8a; }"
            "QPushButton:pressed { background-color: #03045e; }"
            "QPushButton:disabled { background-color: #e0e0e0; color: #bdbdbd; }"
        )
        self.monitor_btn.setIcon(QtGui.QIcon(":/icons/play-circle.svg"))
        self.monitor_btn.setMinimumHeight(100)
        self.monitor_btn.setMinimumWidth(150)
        operation_button_wrapper_layout.addWidget(self.monitor_btn)
        operation_group.setLayout(operation_button_wrapper_layout)
        panel_layout.addWidget(operation_group, 1, 1, Qt.AlignmentFlag.AlignTop)
        panel_layout.setRowStretch(2, 1)
        self.monitor_btn.clicked.connect(self._toggle_monitoring)
        for cb in self.detector_cbs:
            cb.stateChanged.connect(self._detector_selection_changed)

    def on_instrument_connected(self, is_connected: bool):
        logger.info(f"Monitor Panel: Instrument connected = {is_connected}")
        self.is_instrument_connected = is_connected

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

        self.monitor_btn.setEnabled(
            is_connected
        )  # Button itself is enabled if connected

        if not is_connected and self.monitoring:
            logger.warning(
                "Monitor Panel: Instrument disconnected during monitoring. Stopping."
            )
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
            self._stop_monitoring()  # User-initiated stop

    def _apply_monitoring_settings(self) -> bool:
        if not self.is_instrument_connected or self.ct400 is None:
            logger.error(
                "Monitor Panel: Cannot apply settings, instrument not connected."
            )
            return False
        try:
            wavelength = float(self.wavelength_input.text())
            power_mw = self._get_laser_power_mw()
            input_port_enum = LaserInput(int(self.input_port.currentText()))
            det_enables = [
                Enable.ENABLE if cb.isChecked() else Enable.DISABLE
                for cb in self.detector_cbs
            ]
            if len(det_enables) == 4:
                self.ct400.set_detector_array(
                    det_enables[1], det_enables[2], det_enables[3], Enable.DISABLE
                )
            else:
                logger.error(
                    f"Monitor Panel: Incorrect number of detector checkboxes ({len(det_enables)})."
                )
                return False
            self.ct400.cmd_laser(input_port_enum, Enable.ENABLE, wavelength, power_mw)
            logger.info(
                f"Monitor Panel: Laser commanded for monitoring. Port {input_port_enum.value}, WL {wavelength}nm, P {power_mw:.3f}mW"
            )
            return True
        except ValueError as ve:
            QMessageBox.critical(
                self, "Invalid Input", f"Invalid monitoring parameter: {ve}"
            )
            logger.warning(f"Monitor Panel: Settings validation failed: {ve}")
            return False
        except CT400Error as ce:
            QMessageBox.critical(
                self, "Instrument Error", f"Failed to apply settings: {ce}"
            )
            logger.error(f"Monitor Panel: CT400 Error applying settings: {ce}")
            return False
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Unexpected error applying settings: {e}"
            )
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
        self.monitor_btn.setText("Stop Monitoring")
        self.monitor_btn.setIcon(QtGui.QIcon(":/icons/stop-circle.svg"))
        # Style for "Stop Monitoring":
        self.monitor_btn.setStyleSheet(
            "QPushButton {"
            "   background-color: #85182a;"  # Red
            "   color: white;"
            "   font-weight: bold;"
            "   border: none;"
            "   padding: 10px 15px;"
            "   border-radius: 3px;"
            "}"
            "QPushButton:hover { background-color: #6e1423; }"
            "QPushButton:pressed { background-color: #641220; }"
        )
        self.on_instrument_connected(True)  # Updates UI enable states for config parts
        self.timer.start()
        logger.debug(
            f"Monitor Panel: Timer started (Interval: {self.timer.interval()}ms)."
        )

    def _stop_monitoring(self, instrument_error_or_disconnect=False):
        if not self.monitoring and not instrument_error_or_disconnect:
            # If not monitoring and not forced by an error/disconnect, nothing to do.
            # This check is important to prevent issues if called multiple times.
            logger.debug(
                "Monitor Panel: _stop_monitoring called but not active or not forced."
            )
            return

        logger.info(
            f"Monitor Panel: Stopping power monitoring. Forced by error/disconnect: {instrument_error_or_disconnect}"
        )

        # Store current state to know if we need to attempt laser disable
        was_actively_monitoring = self.monitoring
        self.monitoring = False  # Set state first to prevent re-entry

        if self.timer.isActive():
            logger.debug("Monitor Panel: Stopping QTimer.")
            self.timer.stop()

        self.monitor_btn.setText("Start Monitoring")
        self.monitor_btn.setIcon(QtGui.QIcon(":/icons/play-circle.svg"))
        # Style for "Start Monitoring": Blue (back to initial)
        self.monitor_btn.setStyleSheet(
            "QPushButton {"
            "   background-color: #0077b6;"  # Blue
            "   color: white;"
            "   font-weight: bold;"
            "   border: none;"
            "   padding: 10px 15px;"  # Adjust padding as needed
            "   border-radius: 3px;"
            "}"
            "QPushButton:hover { background-color: #023e8a; }"
            "QPushButton:pressed { background-color: #03045e; }"
            "QPushButton:disabled { background-color: #e0e0e0; color: #bdbdbd; }"
        )

        # This call will re-enable config inputs if instrument is still connected
        self.on_instrument_connected(self.is_instrument_connected)

        # Attempt to disable laser only if it was actively monitoring AND
        # it wasn't stopped due to an instrument error/disconnect (because CT400 might be unresponsive)
        if (
            was_actively_monitoring
            and self.is_instrument_connected
            and self.ct400
            and not instrument_error_or_disconnect
        ):
            try:
                logger.info("Monitor Panel: Disabling laser after monitoring.")
                # Use last configured input port for disabling
                input_port_str = (
                    self.input_port.currentText()
                )  # Get from UI as it should hold last used
                input_port_enum = LaserInput(int(input_port_str))

                # Use a safe, common wavelength and low power for disable command
                safe_wavelength = 1550.0
                safe_power = 1.0  # mW

                self.ct400.cmd_laser(
                    input_port_enum, Enable.DISABLE, safe_wavelength, safe_power
                )
                logger.info(f"Monitor Panel: Laser disabled on port {input_port_str}.")
            except (CT400Error, ValueError) as e:
                logger.error(f"Monitor Panel: Failed to disable laser on stop: {e}")
            except Exception as e:
                logger.exception(
                    f"Monitor Panel: Unexpected error disabling laser on stop: {e}"
                )
        elif instrument_error_or_disconnect:
            logger.warning(
                "Monitor Panel: Skipped disabling laser due to instrument error or disconnect event."
            )

        logger.debug("Monitor Panel: _stop_monitoring finished.")

    @Slot()
    def _fetch_power_update(self):
        if not self.monitoring:
            if self.timer.isActive():
                self.timer.stop()
            return

        if not self.is_instrument_connected or self.ct400 is None:
            logger.error(
                "Monitor Panel: Cannot fetch power, instrument not connected or None."
            )
            self._stop_monitoring(instrument_error_or_disconnect=True)
            QMessageBox.critical(
                self, "Connection Error", "CT400 connection lost. Monitoring stopped."
            )
            return

        try:
            power_data_tuple: PowerData = self.ct400.get_all_powers()
            logger.debug(
                f"Monitor Panel: Raw CT400 power data: Pout={power_data_tuple.pout}, Detectors={power_data_tuple.detectors}"
            )

            detector_string_map = {
                Detector.DE_1: "Det 1",
                Detector.DE_2: "Det 2",
                Detector.DE_3: "Det 3",
                Detector.DE_4: "Det 4",
            }

            mapped_detector_values = {}
            if power_data_tuple.detectors:
                for i, enum_key in enumerate(
                    detector_string_map.keys()
                ):  # Iterate in a defined order
                    # Check if detector_string_map has this enum_key and if self.detector_cbs has corresponding checkbox
                    if enum_key in power_data_tuple.detectors and i < len(
                        self.detector_cbs
                    ):
                        str_key = detector_string_map[enum_key]
                        is_checked = self.detector_cbs[
                            i
                        ].isChecked()  # Check the state of the corresponding checkbox

                        if is_checked:
                            mapped_detector_values[str_key] = (
                                power_data_tuple.detectors[enum_key]
                            )
                        else:
                            mapped_detector_values[str_key] = (
                                0.0  # Substitute with 0.0 if unchecked
                            )
                    elif enum_key not in power_data_tuple.detectors:
                        # If CT400 doesn't provide data for an expected detector (even if checked), set to 0 or error indicator
                        str_key = detector_string_map.get(enum_key)
                        if str_key:
                            mapped_detector_values[str_key] = (
                                0.0  # Or perhaps -np.inf if 0 is a valid reading
                            )
                            logger.debug(
                                f"Monitor Panel: No data from CT400 for {str_key}, setting to 0."
                            )
                    # else:
                    #     logger.warning(f"Monitor Panel: Mismatch or missing data for detector enum {enum_key}")

            emit_data = {
                "pout": power_data_tuple.pout,
                "detectors": mapped_detector_values,
            }

            logger.debug(f"Monitor Panel: Emitting processed power data: {emit_data}")
            self.power_data_ready.emit(emit_data)

            # (Optional: your check for all low values can remain)

        except CT400Error as e:
            logger.error(f"Monitor Panel: CT400 Error reading power: {e}")
            self._stop_monitoring(instrument_error_or_disconnect=True)
            QMessageBox.critical(
                self,
                "Monitoring Error",
                f"Failed to read power: {e}\nMonitoring stopped.",
            )
        except Exception as e:
            logger.exception(f"Monitor Panel: Unexpected error reading power: {e}")
            self._stop_monitoring(instrument_error_or_disconnect=True)
            QMessageBox.critical(
                self, "Monitoring Error", f"Unexpected error: {e}\nMonitoring stopped."
            )

    @Slot()
    def _detector_selection_changed(self):
        """Called when a detector checkbox state changes."""
        # This method is now fine to be called during monitoring.
        # _fetch_power_update will use the latest checkbox states.

        if (
            self.is_instrument_connected and self.ct400
        ):  # Only try to command CT400 if connected
            logger.info(
                "Monitor Panel: Detector selection changed, re-applying to CT400."
            )
            try:
                det_enables = [
                    Enable.ENABLE if cb.isChecked() else Enable.DISABLE
                    for cb in self.detector_cbs
                ]
                if len(det_enables) == 4:
                    # Tell the CT400 hardware which detectors to actually read data from.
                    # If a detector is unchecked here, CT400 might return a default low value for it.
                    self.ct400.set_detector_array(
                        det_enables[1], det_enables[2], det_enables[3], Enable.DISABLE
                    )
                else:
                    logger.error(
                        f"Monitor Panel: Incorrect detector checkbox count ({len(det_enables)})."
                    )
            except CT400Error as e:
                logger.error(
                    f"Monitor Panel: Failed to update CT400 detector array: {e}"
                )
                # Decide if this error should stop monitoring or just show a warning.
                # For now, let's assume monitoring can continue, but CT400 might not reflect the change.
                QMessageBox.warning(
                    self,
                    "Detector Error",
                    f"Failed to update detector selection on CT400: {e}",
                )
            except Exception as e:
                logger.exception(
                    f"Monitor Panel: Unexpected error updating CT400 detectors: {e}"
                )
                QMessageBox.warning(
                    self, "Detector Error", f"Error updating detectors on CT400: {e}"
                )
        # No need to stop/start monitoring here. _fetch_power_update will pick up the changes.
