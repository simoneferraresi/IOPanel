import logging
import os
import sys
from enum import Enum, auto
from typing import Dict, List, Optional

import numpy as np
from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QSize, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from vmbpy import VmbCameraError, VmbSystem, VmbSystemError

from config_model import AppConfig

try:
    import resources.resources_rc as resources_rc  # noqa: F401
except ImportError:
    print(
        "Warning: Compiled resource file (resources_rc.py) not found. Icons might be missing.",
        file=sys.stderr,
    )

from hardware.camera import VimbaCam
from hardware.ct400 import (
    CT400,
    CT400Error,
    Enable,
    LaserInput,
    LaserSource,
)
from ui.camera_widgets import CameraPanel
from ui.control_panel import CT400ControlPanel, HistogramControlPanel, ScanSettings
from ui.plot_widgets import HistogramWidget, PlotWidget

VIDEO_TIMER_INTERVAL = 50
logger = logging.getLogger("LabApp.main_window")


class CT400Status(Enum):
    """Defines the possible connection states for the CT400 device."""

    CONNECTED = auto()
    DISCONNECTED = auto()
    CONNECTING = auto()
    DISCONNECTING = auto()
    ERROR = auto()
    UNAVAILABLE = auto()
    UNKNOWN = auto()


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        logger.info("Initializing MainWindow...")

        self.cameras: List[VimbaCam] = []
        self.camera_panels: Dict[str, CameraPanel] = {}
        self.ct400_device: Optional[CT400] = None
        self.shared_scan_settings = ScanSettings()
        self.vmb_instance: Optional[VmbSystem] = None
        self.is_ct400_connected_state = False  # Track explicit connection state
        self.camera_control_actions: Dict[str, QAction] = (
            {}
        )  # For camera control menu items
        self.cameras_menu: Optional[QMenu] = None  # To hold camera control actions

        self._start_vimbasystem()
        self._init_instruments()  # Initialize CT400 first
        self._init_ui()  # Setup UI (calls _create_menus, which needs ct400_device status)
        self._load_defaults_from_config()

        if self.vmb_instance:
            self._init_cameras()
        else:
            logger.error("VimbaSystem not active, skipping camera initialization.")
            if hasattr(self, "camera_container") and self.camera_container.layout():
                error_label = QLabel(
                    "Vimba API could not be initialized. Cameras unavailable."
                )
                error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.camera_container.layout().addWidget(error_label)

        self._connect_signals()
        logger.info("MainWindow initialization complete.")

    def _start_vimbasystem(self):
        if self.vmb_instance is not None:
            logger.info("VmbSystem already active.")
            return
        try:
            logger.info("Attempting to start VmbSystem...")
            self.vmb_instance = VmbSystem.get_instance()
            self.vmb_instance.__enter__()
            logger.info("VmbSystem entered successfully.")
        except VmbSystemError as e:
            logger.error(f"Failed to enter VmbSystem: {e}", exc_info=True)
            self.vmb_instance = None
            QMessageBox.critical(
                self,
                "Vimba System Error",
                f"Could not initialize Vimba API: {e}\nCameras will be unavailable.",
            )
        except Exception as e:
            logger.critical(f"Unexpected error starting VmbSystem: {e}", exc_info=True)
            self.vmb_instance = None
            QMessageBox.critical(
                self,
                "Vimba System Error",
                f"An unexpected error occurred while starting Vimba API: {e}\nCameras will be unavailable.",
            )

    def _cleanup_vimbasystem(self):
        if self.vmb_instance:
            logger.info("Attempting to exit VmbSystem...")
            try:
                self.vmb_instance.__exit__(None, None, None)
                logger.info("VmbSystem exited successfully.")
            except VmbSystemError as e:
                logger.error(f"Failed to exit VmbSystem: {e}", exc_info=True)
            except Exception as e:
                logger.critical(
                    f"Unexpected error exiting VmbSystem: {e}", exc_info=True
                )
            finally:
                self.vmb_instance = None

    def _load_defaults_from_config(self):
        """Load default values from config into UI elements."""
        logger.debug("Loading UI defaults from configuration...")
        try:
            # --- Scan Panel Defaults ---
            if hasattr(self, "control_panel") and self.control_panel:
                scan_defaults = self.config.scan_defaults
                self.control_panel.initial_wl.setText(
                    str(scan_defaults.start_wavelength_nm)
                )
                self.control_panel.final_wl.setText(
                    str(scan_defaults.end_wavelength_nm)
                )
                self.control_panel.resolution.setText(str(scan_defaults.resolution_pm))
                self.control_panel.motor_speed.setText(str(scan_defaults.speed_nm_s))
                self.control_panel.laser_power.setText(str(scan_defaults.laser_power))

                power_unit_idx = self.control_panel.power_unit.findText(
                    scan_defaults.power_unit
                )
                if power_unit_idx != -1:
                    self.control_panel.power_unit.setCurrentIndex(power_unit_idx)

                input_port_idx = self.control_panel.input_port.findText(
                    str(scan_defaults.input_port)
                )
                if input_port_idx != -1:
                    self.control_panel.input_port.setCurrentIndex(input_port_idx)

            # --- Histogram Panel Defaults ---
            if hasattr(self, "histogram_control") and self.histogram_control:
                hist_defaults = self.config.histogram_defaults
                self.histogram_control.wavelength_input.setText(
                    str(hist_defaults.wavelength_nm)
                )
                self.histogram_control.laser_power.setText(
                    str(hist_defaults.laser_power)
                )

                power_unit_idx_hist = self.histogram_control.power_unit.findText(
                    hist_defaults.power_unit
                )
                if power_unit_idx_hist != -1:
                    self.histogram_control.power_unit.setCurrentIndex(
                        power_unit_idx_hist
                    )

                input_port_idx_hist = self.histogram_control.input_port.findText(
                    str(hist_defaults.input_port)
                )
                if input_port_idx_hist != -1:
                    self.histogram_control.input_port.setCurrentIndex(
                        input_port_idx_hist
                    )

                if hasattr(self.histogram_control, "detector_cbs"):
                    self.histogram_control.detector_cbs[0].setChecked(
                        hist_defaults.detector_1_enabled
                    )
                    self.histogram_control.detector_cbs[1].setChecked(
                        hist_defaults.detector_2_enabled
                    )
                    self.histogram_control.detector_cbs[2].setChecked(
                        hist_defaults.detector_3_enabled
                    )
                    self.histogram_control.detector_cbs[3].setChecked(
                        hist_defaults.detector_4_enabled
                    )

        except Exception as e:
            logger.error(f"Error loading defaults from config: {e}", exc_info=True)
            if hasattr(self, "statusBar") and self.statusBar:
                self.statusBar.showMessage("Error loading defaults from config", 5000)
            else:
                logger.error("Cannot show status message: statusBar not available.")

    def _init_instruments(self):
        logger.info("Initializing instruments...")
        # --- REFACTOR: Direct attribute access ---
        dll_path = self.config.instruments.ct400_dll_path
        self.ct400_device = None  # Ensure it's None before attempting init

        if not dll_path:
            msg = "CT400 DLL path not found in configuration."
            logger.error(msg)
            return

        if not os.path.exists(dll_path):
            msg = f"CT400 DLL path does not exist: {dll_path}"
            logger.error(msg)
            return

        try:
            self.ct400_device = CT400(dll_path)
            logger.info(f"CT400 device object created using DLL: {dll_path}")
        except (CT400Error, FileNotFoundError, OSError) as e:
            logger.error(f"Failed to initialize CT400 device: {e}", exc_info=True)
        except Exception as e:
            logger.critical(
                f"Unexpected error during CT400 initialization: {e}", exc_info=True
            )

    def _init_ui(self):
        logger.debug("Initializing UI...")
        app_name = self.config.app_name
        self.setWindowTitle(app_name)

        icon_path = ":/icons/laser.svg"
        if QIcon.hasThemeIcon(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            fallback_icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "resources", "laser.svg"
            )
            if os.path.exists(fallback_icon_path):
                self.setWindowIcon(QtGui.QIcon(fallback_icon_path))
            else:
                logger.warning(
                    f"Application icon not found at '{icon_path}' or '{fallback_icon_path}'."
                )

        self.setMinimumSize(1200, 800)
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            # --- REFACTOR: Direct attribute access ---
            width_ratio = self.config.ui.initial_width_ratio
            height_ratio = self.config.ui.initial_height_ratio
            self.resize(
                int(screen.width() * width_ratio), int(screen.height() * height_ratio)
            )
        except Exception as e:
            logger.warning(f"Could not set initial size based on screen: {e}")
            self.resize(1280, 850)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        self.camera_container = QWidget()
        cam_layout = QHBoxLayout(self.camera_container)
        cam_layout.setContentsMargins(0, 0, 0, 0)
        cam_layout.setSpacing(5)

        control_container = QWidget()
        control_layout = QVBoxLayout(control_container)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(5)
        self.tab_widget = QTabWidget()
        self.tab_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        control_layout.addWidget(self.tab_widget)

        splitter.addWidget(self.camera_container)
        splitter.addWidget(control_container)
        initial_cam_height = int(self.height() * 0.6)
        splitter.setSizes([initial_cam_height, self.height() - initial_cam_height])
        main_layout.addWidget(splitter)

        self.first_tab = QWidget()
        first_tab_layout = QHBoxLayout(self.first_tab)
        first_tab_layout.setSpacing(5)
        self.control_panel = CT400ControlPanel(
            self.shared_scan_settings, self.ct400_device, self.config
        )
        self.plot_widget = PlotWidget(self.shared_scan_settings)
        first_tab_layout.addWidget(self.control_panel, stretch=0)
        first_tab_layout.addWidget(self.plot_widget, stretch=1)
        self.tab_widget.addTab(self.first_tab, "Wavelength Scan")

        self.second_tab = QWidget()
        second_tab_layout = QHBoxLayout(self.second_tab)
        second_tab_layout.setSpacing(5)
        self.histogram_control = HistogramControlPanel(self.ct400_device, self.config)
        hist_detector_keys = [cb.text() for cb in self.histogram_control.detector_cbs]
        self.histogram_widget = HistogramWidget(
            self.histogram_control, hist_detector_keys
        )
        second_tab_layout.addWidget(self.histogram_control, stretch=0)
        second_tab_layout.addWidget(self.histogram_widget, stretch=1)
        self.tab_widget.addTab(self.second_tab, "Power Monitor")

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.ct400_status_label = QLabel("CT400: Unknown")
        self.ct400_status_label.setObjectName("ct400StatusLabel")
        self.ct400_status_label.setMinimumWidth(180)
        self.ct400_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.statusBar.addPermanentWidget(self.ct400_status_label)
        self.statusBar.showMessage("Ready.")

        self._create_menus()

        if self.ct400_device:
            self._update_ct400_visuals(
                state=CT400Status.DISCONNECTED, message="CT400 Ready (Disconnected)"
            )
        else:
            self._update_ct400_visuals(
                state=CT400Status.UNAVAILABLE,
                message="CT400 Unavailable/Not Initialized",
            )

        logger.debug("UI Initialization finished.")

    def _update_ct400_visuals(
        self,
        state: CT400Status,
        message: Optional[str] = None,
        status_bar_timeout: int = 5000,
    ):
        """
        Updates all UI elements related to the CT400 connection status using a typesafe Enum.

        This is the single source of truth for the visual state of the CT400.
        It updates the menu/toolbar action, the status bar label, and informs
        the control panels so they can enable/disable their own widgets.

        Args:
            state: A CT400Status enum member representing the new state.
            message: An optional message to display in the main status bar.
            status_bar_timeout: The duration (in ms) to show the status bar message.
        """
        action_enabled = True
        action_checked = False

        if hasattr(self, "ct400_connect_action"):
            action = self.ct400_connect_action
            if state == CT400Status.CONNECTED:
                action.setText("Disconnect CT400")
                action.setIcon(QIcon(":/icons/disconnect.svg"))
                action_checked = True
            elif state == CT400Status.CONNECTING:
                action.setText("Connecting...")
                action.setIcon(QIcon(":/icons/spinner.svg"))
                action_enabled = False
                action_checked = True
            elif state == CT400Status.DISCONNECTING:
                action.setText("Disconnecting...")
                action.setIcon(QIcon(":/icons/spinner.svg"))
                action_enabled = False
                action_checked = False
            elif state == CT400Status.ERROR:
                action.setText("Connect CT400 (Error)")
                action.setIcon(QIcon(":/icons/laser.svg"))
                action_checked = False
                # Use a class constant for the timeout
                _ERROR_STATE_RESET_MS = 3000
                QTimer.singleShot(
                    _ERROR_STATE_RESET_MS,
                    lambda: self._update_ct400_visuals(
                        state=CT400Status.DISCONNECTED,
                        message="Error occurred. Ready to connect.",
                    ),
                )
            elif state == CT400Status.UNAVAILABLE:
                action.setText("CT400 Unavailable")
                action.setIcon(QIcon(":/icons/laser.svg"))
                action_enabled = False
                action_checked = False
            else:  # DISCONNECTED or UNKNOWN
                action.setText("Connect CT400")
                action.setIcon(QIcon(":/icons/connect.svg"))
                action_checked = False

            action.setEnabled(action_enabled and (self.ct400_device is not None))
            action.setChecked(action_checked)

        if hasattr(self, "ct400_status_label"):
            label = self.ct400_status_label
            label_text = f"CT400: {state.name.title()}"
            status_property = state.name.lower()  # e.g., "connected", "error"

            label.setText(label_text)
            try:
                # This ensures the style is reapplied based on the new property
                label.setProperty("status", status_property)
                label.style().unpolish(label)
                label.style().polish(label)
                label.update()
            except Exception as e:
                logger.warning(f"Could not apply style for CT400 status label: {e}")

        if message:
            is_transient = state in [
                CT400Status.CONNECTING,
                CT400Status.DISCONNECTING,
                CT400Status.ERROR,
                CT400Status.UNAVAILABLE,
            ]
            timeout = 0 if is_transient else status_bar_timeout
            self.statusBar.showMessage(message, timeout)

        is_mw_logically_connected = state == CT400Status.CONNECTED
        panel_can_use_device = is_mw_logically_connected and (
            self.ct400_device is not None
        )

        if hasattr(self, "control_panel") and self.control_panel:
            self.control_panel.on_instrument_connected(panel_can_use_device)
        if hasattr(self, "histogram_control") and self.histogram_control:
            self.histogram_control.on_instrument_connected(panel_can_use_device)

        self.is_ct400_connected_state = is_mw_logically_connected

    def _create_menus(self):
        logger.debug("Creating menus...")
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        exit_action = QAction(QIcon(":/icons/exit.svg"), "E&xit", self)
        exit_action.setStatusTip("Exit the application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self.instrument_menu = menu_bar.addMenu("&Instruments")
        self.ct400_connect_action = QAction(self)
        self.ct400_connect_action.setCheckable(True)
        self.ct400_connect_action.setStatusTip("Connect/Disconnect CT400 device")
        self.ct400_connect_action.triggered.connect(
            self._handle_ct400_connect_action_triggered
        )
        self.instrument_menu.addAction(self.ct400_connect_action)

        self.cameras_menu = menu_bar.addMenu("&Cameras")

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.setStatusTip("Show application information")
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)
        logger.debug("Menus created.")

    def _handle_ct400_connect_action_triggered(self, checked: bool):
        """
        Handles the triggered signal from the QAction.
        'checked' indicates the NEW state of the action if it were a simple toggle.
        We use this to decide whether to connect or disconnect.
        """
        if not self.ct400_device:
            self._update_ct400_visuals(
                state="unavailable", message="CT400 Not Initialized."
            )
            if hasattr(self, "ct400_connect_action"):
                self.ct400_connect_action.setChecked(False)
            return

        if checked:
            self._initiate_ct400_connection()
        else:
            self._initiate_ct400_disconnection()

    def _initiate_ct400_connection(self):
        self._update_ct400_visuals(
            state=CT400Status.CONNECTING, message="CT400: Attempting to connect..."
        )
        QApplication.processEvents()

        try:
            gpib = self.config.instruments.tunics_gpib_address
            laser_input = LaserInput(self.config.scan_defaults.input_port)
            min_wl = self.config.scan_defaults.min_wavelength_nm
            max_wl = self.config.scan_defaults.max_wavelength_nm
            speed = self.config.scan_defaults.speed_nm_s

            self.ct400_device.set_laser(
                laser_input=laser_input,
                enable=Enable.ENABLE,
                gpib_address=gpib,
                laser_type=LaserSource.LS_TunicsT100s_HP,
                min_wavelength=min_wl,
                max_wavelength=max_wl,
                speed=speed,
            )
            logger.info(f"CT400 Connected (GPIB: {gpib}, Input: {laser_input.value}).")
            self._update_ct400_visuals(
                state=CT400Status.CONNECTED,
                message=f"CT400 Connected (Input {laser_input.value})",
            )
        except (CT400Error, ValueError, KeyError, Exception) as e:
            logger.error(f"CT400 connection failed: {e}", exc_info=True)
            self._update_ct400_visuals(
                state=CT400Status.ERROR, message=f"Connection Failed: {e}"
            )

    def _initiate_ct400_disconnection(self):
        self._update_ct400_visuals(
            state=CT400Status.DISCONNECTING, message="CT400: Disconnecting..."
        )
        QApplication.processEvents()

        try:
            laser_input_disconnect = LaserInput(self.config.scan_defaults.input_port)
            safe_wl = self.config.scan_defaults.safe_parking_wavelength
            safe_power = self.config.scan_defaults.laser_power

            self.ct400_device.cmd_laser(
                laser_input=laser_input_disconnect,
                enable=Enable.DISABLE,
                wavelength=safe_wl,
                power=safe_power,
            )
            logger.info("CT400 Disconnected.")
            self._update_ct400_visuals(
                state=CT400Status.DISCONNECTED, message="CT400 Disconnected"
            )
        except (CT400Error, ValueError, KeyError, Exception) as e:
            logger.error(f"Error during CT400 disconnection: {e}", exc_info=True)
            self._update_ct400_visuals(
                state=CT400Status.ERROR, message=f"Disconnect Failed: {e}"
            )

    def _show_about_dialog(self):
        # --- REFACTOR: Direct attribute access ---
        app_name = self.config.app_name
        app_version = QApplication.applicationVersion()
        QMessageBox.about(
            self,
            f"About {app_name}",
            f"<b>{app_name}</b><br>"
            f"Version: {app_version}<br><br>"
            "IOP Lab Control App.<br>"
            "(c) 2024 Simone Ferraresi/IOP Lab @ UniFe",
        )

    def _init_cameras(self):
        logger.info("Initializing cameras dynamically from config...")
        if not self.camera_container or not self.camera_container.layout():
            logger.error(
                "Camera container or layout not found. Cannot initialize cameras."
            )
            return
        if not self.cameras_menu:
            logger.error(
                "Cameras menu not initialized. Cannot add camera control actions."
            )
            return

        self._cleanup_cameras()
        camera_layout = self.camera_container.layout()
        if (
            not isinstance(camera_layout, QtWidgets.QHBoxLayout)
            and not isinstance(camera_layout, QtWidgets.QVBoxLayout)
            and not isinstance(camera_layout, QtWidgets.QGridLayout)
        ):
            logger.error(
                f"Camera container layout is not a recognized QLayout type: {type(camera_layout)}"
            )
            if self.camera_container.layout() is None:
                new_layout = QHBoxLayout(self.camera_container)
                new_layout.setContentsMargins(0, 0, 0, 0)
                new_layout.setSpacing(5)
                camera_layout = new_layout
            else:
                return

        cameras_initialized_count = 0
        cameras_attempted_count = 0

        # --- REFACTOR: This loop now correctly uses the typed AppConfig object ---
        for section_name, cam_config in self.config.cameras.items():
            cameras_attempted_count += 1

            if not cam_config.enabled:
                logger.info(f"Skipping disabled camera: {cam_config.name}")
                continue

            if not cam_config.identifier or cam_config.identifier.startswith("PUT_"):
                logger.warning(
                    f"Skipping camera '{cam_config.name}': Invalid or placeholder identifier."
                )
                error_msg = f"{cam_config.name}\n(Config Error: Invalid ID)"
                placeholder = self._create_camera_error_placeholder(error_msg)
                camera_layout.addWidget(placeholder)
                continue

            logger.info(
                f"Attempting to initialize camera: {cam_config.name} (ID: {cam_config.identifier})"
            )
            cam_instance = None
            try:
                cam_instance = VimbaCam(
                    identifier=cam_config.identifier,
                    camera_name=cam_config.name,
                    flip_horizontal=cam_config.flip_horizontal,
                    parent=self,
                )

                if not cam_instance.open():
                    logger.error(
                        f"Failed to open camera {cam_config.name} (ID: {cam_config.identifier}). Skipping panel."
                    )
                    error_msg = f"{cam_config.name}\n(Failed to Open)"
                    placeholder = self._create_camera_error_placeholder(error_msg)
                    camera_layout.addWidget(placeholder)
                    if cam_instance:
                        cam_instance.close()
                    continue

                panel = CameraPanel(
                    cam_instance,
                    cam_config.name,
                    config=cam_config,
                    parent=self.camera_container,
                )
                camera_layout.addWidget(panel)
                self.cameras.append(cam_instance)
                self.camera_panels[cam_config.identifier] = panel

                cam_instance.new_frame.connect(panel.process_new_frame_data)
                if hasattr(panel, "update_fps"):
                    cam_instance.fps_updated.connect(panel.update_fps)

                action = QAction(self)
                action.setCheckable(True)
                action.setChecked(panel.get_controls_visible())
                action.setText(
                    f"{'Hide' if panel.get_controls_visible() else 'Show'} {cam_config.name} Controls"
                )
                action.setData(cam_config.identifier)
                action.triggered.connect(self._handle_camera_control_toggle)
                self.cameras_menu.addAction(action)
                self.camera_control_actions[cam_config.identifier] = action

                logger.info(
                    f"Successfully initialized and connected signals for camera: {cam_config.name}"
                )
                cameras_initialized_count += 1

            except VmbCameraError as e:
                logger.error(
                    f"Vimba Error initializing camera {cam_config.name} (ID: {cam_config.identifier}): {e}"
                )
                QMessageBox.warning(
                    self, "Camera Error", f"Vimba error for {cam_config.name}:\n{e}"
                )
                error_msg = f"{cam_config.name}\n(Vimba Error)"
                placeholder = self._create_camera_error_placeholder(error_msg)
                camera_layout.addWidget(placeholder)
                if cam_instance:
                    cam_instance.close()
            except Exception as e:
                logger.error(
                    f"Unexpected error initializing camera {cam_config.name} (ID: {cam_config.identifier}): {e}",
                    exc_info=True,
                )
                QMessageBox.critical(
                    self, "Camera Error", f"Critical error for {cam_config.name}:\n{e}"
                )
                error_msg = f"{cam_config.name}\n(Initialization Error)"
                placeholder = self._create_camera_error_placeholder(error_msg)
                camera_layout.addWidget(placeholder)
                if cam_instance:
                    cam_instance.close()

        if cameras_attempted_count > 0 and cameras_initialized_count == 0:
            logger.warning(
                "No cameras were successfully initialized out of those attempted."
            )
            if self.cameras_menu and not self.cameras_menu.actions():
                no_cam_action = QAction("No cameras available/configured", self)
                no_cam_action.setEnabled(False)
                self.cameras_menu.addAction(no_cam_action)

        elif cameras_attempted_count == 0:
            logger.info("No camera sections found in configuration.")
            placeholder_label = QLabel("No cameras configured in settings.")
            placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            camera_layout.addWidget(placeholder_label)
            if self.cameras_menu:
                no_cam_action = QAction("No cameras configured", self)
                no_cam_action.setEnabled(False)
                self.cameras_menu.addAction(no_cam_action)
        else:
            logger.info(
                f"Total cameras initialized: {cameras_initialized_count} out of {cameras_attempted_count} attempted."
            )

    def _create_camera_error_placeholder(self, message: str) -> QWidget:
        """Helper to create a consistent placeholder for camera errors."""
        placeholder_widget = QFrame()
        placeholder_widget.setObjectName("cameraErrorPlaceholder")
        placeholder_widget.setStyleSheet(
            "QFrame#cameraErrorPlaceholder {"
            "  border: 1px solid #ffcdd2;"
            "  border-radius: 5px;"
            "  background-color: #ffebee;"
            "}"
            "QLabel { color: #c62828; font-weight: normal; background: transparent; }"
        )
        placeholder_widget.setMinimumSize(220, 120)
        placeholder_widget.setMaximumWidth(350)
        placeholder_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )

        layout = QVBoxLayout(placeholder_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        icon_label = QLabel()
        try:
            std_icon = self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning
            )
            if not std_icon.isNull():
                icon_label.setPixmap(std_icon.pixmap(QSize(32, 32)))
        except Exception as e_icon:
            logger.warning(f"Could not load standard warning icon: {e_icon}")
            icon_label.setText("⚠️")
            icon_label.setFont(QFont("Segoe UI Symbol", 20))

        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_label = QLabel(message)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setWordWrap(True)

        layout.addWidget(icon_label)
        layout.addWidget(text_label)

        placeholder_widget.setToolTip(
            f"Camera Initialization Error: {message.replace('\n', ' ')}"
        )
        return placeholder_widget

    @Slot(bool)
    def _handle_camera_control_toggle(self, checked: bool):
        action = self.sender()
        if not isinstance(action, QAction):
            return

        camera_identifier = action.data()
        if camera_identifier in self.camera_panels:
            panel = self.camera_panels[camera_identifier]
            panel.set_controls_visibility(checked)
            action.setText(
                f"{'Hide' if checked else 'Show'} {panel._panel_title} Controls"
            )
        else:
            logger.warning(
                f"Camera panel not found for identifier: {camera_identifier} during toggle."
            )
            action.setChecked(not checked)
            action.setEnabled(False)

    @Slot(object, object, object)
    def _handle_scan_data(self, wavelengths, plotting_power_data, final_pout):
        logger.debug(
            f"Received scan data signal. Wavelength points: {len(wavelengths)}"
        )
        output_power = final_pout
        logger.info(f"Handling scan data with output power: {output_power}")
        if not isinstance(plotting_power_data, np.ndarray):
            try:
                plotting_power_data = np.asarray(plotting_power_data)
            except Exception:
                logger.error("Could not convert plotting_power_data to numpy array.")
                plotting_power_data = np.array([])

        if self.plot_widget and hasattr(self.plot_widget, "update_plot"):
            try:
                self.plot_widget.update_plot(
                    wavelengths, plotting_power_data, output_power
                )
            except Exception as e:
                logger.error(f"Error updating plot widget: {e}", exc_info=True)

    @Slot(dict)
    def handle_power_data(self, power_data: Dict):
        logger.debug(f"Received power data: {power_data}")
        if self.histogram_widget and hasattr(self.histogram_widget, "schedule_update"):
            try:
                self.histogram_widget.schedule_update(power_data)
            except Exception as e:
                logger.error(f"Error updating histogram widget: {e}", exc_info=True)
        else:
            logger.warning(
                "Histogram widget not available or does not have schedule_update method."
            )

    def _cleanup_cameras(self):
        logger.info(f"Closing {len(self.cameras)} camera(s)...")

        if hasattr(self, "cameras_menu") and self.cameras_menu is not None:
            for cam_id in list(self.camera_control_actions.keys()):
                action = self.camera_control_actions.pop(cam_id, None)
                if action:
                    self.cameras_menu.removeAction(action)
                    action.deleteLater()
        self.camera_control_actions.clear()

        if (
            hasattr(self, "camera_container")
            and self.camera_container.layout() is not None
        ):
            layout = self.camera_container.layout()
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

        cameras_to_close = list(self.cameras)
        self.cameras.clear()
        self.camera_panels.clear()

        for cam in cameras_to_close:
            if cam is not None:
                logger.debug(f"Closing camera: {cam.camera_name}")
                try:
                    cam.close()
                except Exception as e:
                    logger.error(
                        f"Error closing camera {cam.camera_name}: {e}", exc_info=True
                    )
        logger.info("Camera cleanup finished.")

    def cleanup(self):
        logger.info("Performing MainWindow cleanup...")
        if hasattr(self, "histogram_control"):
            self.histogram_control.cleanup_worker_thread()  # Already well-named
        if hasattr(self, "control_panel") and self.control_panel.scanning:
            self.control_panel._stop_scan(cancelled=True)
        if hasattr(self, "plot_widget"):
            self.plot_widget.cleanup()  # Call the new method

        self._cleanup_cameras()

        if self.ct400_device:
            logger.debug("Closing central CT400 device...")
            try:
                if self.is_ct400_connected_state:
                    try:
                        # --- REFACTOR: Direct attribute access ---
                        laser_input_disconnect = LaserInput(
                            self.config.scan_defaults.input_port
                        )
                        self.ct400_device.cmd_laser(
                            laser_input_disconnect,
                            Enable.DISABLE,
                            1550.0,
                            1.0,
                        )
                        logger.info("CT400 laser disabled during cleanup.")
                    except Exception as e_cmd:
                        logger.warning(
                            f"Could not disable CT400 laser during cleanup: {e_cmd}"
                        )
                self.ct400_device.close()
                logger.info("CT400 device closed.")
            except Exception as e:
                logger.error(f"Error closing CT400 device: {e}", exc_info=True)
            self.ct400_device = None

        self._cleanup_vimbasystem()
        logger.info("MainWindow cleanup finished.")

    def closeEvent(self, event: QtGui.QCloseEvent):
        logger.info("Close event triggered for MainWindow.")
        event.accept()

    def _connect_signals(self):
        if hasattr(self, "control_panel") and self.control_panel:
            logger.debug("Connecting control_panel signals")
            self.control_panel.scan_data_ready.connect(self._handle_scan_data)
            self.control_panel.progress_updated.connect(
                lambda value: self.statusBar.showMessage(
                    f"Scan Progress: {value}%", 1000 if value < 100 else 0
                )
            )
        else:
            logger.warning(
                "CT400 Control Panel not initialized, skipping signal connection."
            )

        if hasattr(self, "histogram_control") and self.histogram_control:
            logger.debug("Connecting histogram_control signals")
            self.histogram_control.power_data_ready.connect(self.handle_power_data)
        else:
            logger.warning(
                "Histogram Control Panel not initialized, skipping signal connection."
            )
