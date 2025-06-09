import logging
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np

# Make sure PySide6 is used consistently
from PySide6 import QtGui
from PySide6.QtCore import Qt, QTimer, Slot  # Added QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,  # Added QMenu
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from vmbpy import VmbCameraError, VmbSystem, VmbSystemError

try:
    import resources_rc
except ImportError:
    print(
        "Warning: Compiled resource file (resources_rc.py) not found. Icons might be missing.",
        file=sys.stderr,
    )

from camera import VimbaCam
from control_panel import CT400ControlPanel, HistogramControlPanel, ScanSettings
from CT400_updated import (
    CT400,
    CT400Error,
    Enable,
    LaserInput,
    LaserSource,
)
from gui_panels import CameraPanel, HistogramWidget, PlotWidget

VIDEO_TIMER_INTERVAL = 50
logger = logging.getLogger("LabApp.main_window")


class MainWindow(QMainWindow):
    def __init__(self, config: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = config
        logger.info("Initializing MainWindow...")

        self.cameras: List[VimbaCam] = []
        self.camera_panels: Dict[str, CameraPanel] = {}
        self.ct400_device: Optional[CT400] = None
        self.shared_scan_settings = ScanSettings()
        self.vmb_instance: Optional[VmbSystem] = None
        self.is_ct400_connected_state = False  # Track explicit connection state
        self.camera_control_actions: Dict[
            str, QAction
        ] = {}  # For camera control menu items
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
            # Scan Panel Defaults
            if hasattr(self, "control_panel") and self.control_panel:
                scan_defaults = self.config.get("ScanDefaults", {})
                self.control_panel.initial_wl.setText(
                    str(scan_defaults.get("start_wavelength_nm", "1550.0"))
                )
                self.control_panel.final_wl.setText(
                    str(scan_defaults.get("end_wavelength_nm", "1560.0"))
                )
                self.control_panel.resolution.setText(
                    str(scan_defaults.get("resolution_pm", "1"))
                )
                self.control_panel.motor_speed.setText(
                    str(scan_defaults.get("speed_nm_s", "10"))
                )
                self.control_panel.laser_power.setText(
                    str(scan_defaults.get("laser_power", "1.0"))
                )
                power_unit_idx = self.control_panel.power_unit.findText(
                    scan_defaults.get("power_unit", "mW")
                )
                if power_unit_idx != -1:
                    self.control_panel.power_unit.setCurrentIndex(power_unit_idx)

                # Ensure input_port is handled correctly
                input_port_text = str(scan_defaults.get("input_port", "1"))
                input_port_idx = self.control_panel.input_port.findText(input_port_text)
                if input_port_idx != -1:
                    self.control_panel.input_port.setCurrentIndex(input_port_idx)
                else:
                    logger.warning(
                        f"ScanDefaults: Input port '{input_port_text}' not found in ComboBox. Using default."
                    )
                    self.control_panel.input_port.setCurrentIndex(
                        0
                    )  # Fallback to first item

                # Update shared settings initially
                self.control_panel.update_shared_settings()

            # Histogram Panel Defaults
            if hasattr(self, "histogram_control") and self.histogram_control:
                hist_defaults = self.config.get("HistogramDefaults", {})
                self.histogram_control.wavelength_input.setText(
                    str(hist_defaults.get("wavelength_nm", "1550.0"))
                )
                self.histogram_control.laser_power.setText(
                    str(hist_defaults.get("laser_power", "1.0"))
                )
                power_unit_idx_hist = self.histogram_control.power_unit.findText(
                    hist_defaults.get("power_unit", "mW")
                )
                if power_unit_idx_hist != -1:
                    self.histogram_control.power_unit.setCurrentIndex(
                        power_unit_idx_hist
                    )

                # Ensure input_port for histogram is handled correctly
                hist_input_port_text = str(hist_defaults.get("input_port", "1"))
                input_port_idx_hist = self.histogram_control.input_port.findText(
                    hist_input_port_text
                )

                if input_port_idx_hist != -1:
                    self.histogram_control.input_port.setCurrentIndex(
                        input_port_idx_hist
                    )
                else:
                    logger.warning(
                        f"HistogramDefaults: Input port '{hist_input_port_text}' not found in ComboBox. Using default."
                    )
                    self.histogram_control.input_port.setCurrentIndex(
                        0
                    )  # Fallback to first item

                # Set detector checkboxes
                if hasattr(self.histogram_control, "detector_cbs"):
                    for i, cb in enumerate(self.histogram_control.detector_cbs):
                        key = f"detector_{i + 1}_enabled"
                        # Default to "true" if not found in config for safety
                        enabled = str(hist_defaults.get(key, "true")).lower() == "true"
                        cb.setChecked(enabled)
                else:
                    logger.warning(
                        "Histogram control panel does not have 'detector_cbs' attribute."
                    )

        except Exception as e:
            logger.error(f"Error loading defaults from config: {e}", exc_info=True)
            if (
                hasattr(self, "statusBar") and self.statusBar
            ):  # Check if statusBar exists
                self.statusBar.showMessage("Error loading defaults from config", 5000)
            else:
                logger.error("Cannot show status message: statusBar not available.")

    def _init_instruments(self):
        logger.info("Initializing instruments...")
        dll_path = self.config.get("Instruments", {}).get("ct400_dll_path")
        self.ct400_device = None  # Ensure it's None before attempting init

        if not dll_path:
            msg = "CT400 DLL path not found in configuration."
            logger.error(msg)
            # Status updated in _init_ui
            return

        if not os.path.exists(dll_path):
            msg = f"CT400 DLL path does not exist: {dll_path}"
            logger.error(msg)
            # Status updated in _init_ui
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
        # Visual status updated in _init_ui after this call

    def _init_ui(self):
        logger.debug("Initializing UI...")
        app_name = self.config.get("App", {}).get("name", "Lab Control")
        self.setWindowTitle(app_name)

        icon_path = ":/icons/laser.svg"
        if QIcon.hasThemeIcon(icon_path):  # Check with QIcon directly
            self.setWindowIcon(QIcon(icon_path))
        else:  # Fallback if resources_rc not compiled or icon missing
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
            width_ratio = float(
                self.config.get("UI", {}).get("initial_width_ratio", 0.8)
            )
            height_ratio = float(
                self.config.get("UI", {}).get("initial_height_ratio", 0.8)
            )
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
        first_tab_layout.addWidget(
            self.control_panel, stretch=0
        )  # Control panel takes preferred width
        first_tab_layout.addWidget(
            self.plot_widget, stretch=1
        )  # Plot widget takes all remaining space
        self.tab_widget.addTab(self.first_tab, "Wavelength Scan")

        self.second_tab = QWidget()
        second_tab_layout = QHBoxLayout(self.second_tab)
        second_tab_layout.setSpacing(5)
        self.histogram_control = HistogramControlPanel(self.ct400_device, self.config)
        hist_detector_keys = [
            cb.text() for cb in self.histogram_control.detector_cbs
        ]  # Get names
        self.histogram_widget = HistogramWidget(
            self.histogram_control, hist_detector_keys
        )
        second_tab_layout.addWidget(
            self.histogram_control, stretch=0
        )  # Control panel takes minimum width
        second_tab_layout.addWidget(
            self.histogram_widget, stretch=1
        )  # Histogram widget takes all remaining space
        self.tab_widget.addTab(self.second_tab, "Power Monitor")

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.ct400_status_label = QLabel("CT400: Unknown")
        self.ct400_status_label.setObjectName("ct400StatusLabel")
        self.ct400_status_label.setMinimumWidth(180)  # Adjusted width
        self.ct400_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.statusBar.addPermanentWidget(self.ct400_status_label)
        self.statusBar.showMessage("Ready.")

        self._create_menus()  # Creates self.ct400_connect_action and self.cameras_menu

        # No more toolbar for the time being...
        # self.instrument_toolbar = self.addToolBar("Instruments")
        # self.instrument_toolbar.setObjectName("instrumentToolbar")
        # if hasattr(self, "ct400_connect_action"):
        #     self.instrument_toolbar.addAction(self.ct400_connect_action)
        # else:
        #     logger.error("ct400_connect_action not found for toolbar.")

        # Set initial visual status after all UI elements are created
        if self.ct400_device:
            # Device object exists, but not necessarily "connected" in terms of laser enabled
            self._update_ct400_visuals(
                state="disconnected", message="CT400 Ready (Disconnected)"
            )
        else:
            self._update_ct400_visuals(
                state="unavailable", message="CT400 Unavailable/Not Initialized"
            )

        logger.debug("UI Initialization finished.")

    def _update_ct400_visuals(
        self, state: str, message: Optional[str] = None, status_bar_timeout: int = 5000
    ):
        """
        Centralized method to update all CT400 related visual elements.
        States: "connected", "disconnected", "connecting", "disconnecting", "error", "unavailable", "unknown"
        """
        action_enabled = True
        action_checked = False

        # Update Action Button (Toolbar/Menu)
        if hasattr(self, "ct400_connect_action"):
            action = self.ct400_connect_action
            if state == "connected":
                action.setText("Disconnect CT400")
                action.setIcon(QIcon(":/icons/disconnect.svg"))
                action_checked = True
            elif state == "connecting":
                action.setText("Connecting...")
                action.setIcon(QIcon(":/icons/spinner.svg"))
                action_enabled = False
                action_checked = True
            elif state == "disconnecting":
                action.setText("Disconnecting...")
                action.setIcon(QIcon(":/icons/spinner.svg"))
                action_enabled = False
                action_checked = False
            elif state == "error":
                action.setText("Connect CT400 (Error)")
                action.setIcon(QIcon(":/icons/laser.svg"))
                action_checked = False
                QTimer.singleShot(
                    3000,
                    lambda: self._update_ct400_visuals(
                        state="disconnected",
                        message="Error occurred. Ready to connect.",
                    ),
                )
            elif state == "unavailable":
                action.setText("CT400 Unavailable")
                action.setIcon(QIcon(":/icons/laser.svg"))
                action_enabled = False
                action_checked = False
            else:  # "disconnected" or "unknown"
                action.setText("Connect CT400")
                action.setIcon(QIcon(":/icons/connect.svg"))
                action_checked = False

            action.setEnabled(action_enabled and (self.ct400_device is not None))
            action.setChecked(action_checked)

        # Update Status Label in StatusBar
        if hasattr(self, "ct400_status_label"):
            label = self.ct400_status_label
            base_text = "CT400: "
            label_text = base_text
            status_property = state  # For QSS

            if state == "connected":
                label_text += "Connected"
            elif state == "connecting":
                label_text += "Connecting..."
            elif state == "disconnecting":
                label_text += "Disconnecting..."
            elif state == "error":
                label_text += "Error"
            elif state == "unavailable":
                label_text += "Unavailable"
            elif state == "unknown":
                label_text += "Unknown"
            else:  # "disconnected"
                label_text += "Disconnected"

            label.setText(label_text)
            label.setProperty("status", status_property)
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

        # Update Main Status Bar Message
        if message:
            self.statusBar.showMessage(
                message,
                status_bar_timeout
                if state not in ["connecting", "disconnecting", "error", "unavailable"]
                else 0,
            )

        # Update Control Panels
        is_logically_connected = state == "connected"
        if (
            self.is_ct400_connected_state != is_logically_connected
            or state == "unavailable"
        ):  # Update if state changes
            self.is_ct400_connected_state = is_logically_connected
            if hasattr(self, "control_panel") and self.control_panel:
                self.control_panel.on_instrument_connected(
                    is_logically_connected and (self.ct400_device is not None)
                )
            if hasattr(self, "histogram_control") and self.histogram_control:
                self.histogram_control.on_instrument_connected(
                    is_logically_connected and (self.ct400_device is not None)
                )

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

        # Create Cameras menu
        self.cameras_menu = menu_bar.addMenu("&Cameras")
        # Actions for this menu will be populated in _init_cameras

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
            if hasattr(self, "ct400_connect_action"):  # Ensure action exists
                self.ct400_connect_action.setChecked(False)
            return

        if checked:  # User intends to connect (action is now checked)
            self._initiate_ct400_connection()
        else:  # User intends to disconnect (action is now unchecked)
            self._initiate_ct400_disconnection()

    def _initiate_ct400_connection(self):
        self._update_ct400_visuals(
            state="connecting", message="CT400: Attempting to connect..."
        )
        QApplication.processEvents()  # Allow UI to update

        try:
            gpib_str = self.config.get("Instruments", {}).get(
                "tunics_gpib_address", "10"
            )
            gpib = int(gpib_str)
            port_str = self.config.get("ScanDefaults", {}).get("input_port", "1")
            laser_input = LaserInput(int(port_str))
            min_wl = float(
                self.config.get("ScanDefaults", {}).get("min_wavelength_nm", 1440)
            )
            max_wl = float(
                self.config.get("ScanDefaults", {}).get("max_wavelength_nm", 1640)
            )
            speed = int(
                self.config.get("ScanDefaults", {}).get("speed_nm_s", 10)
            )  # Default speed

            self.ct400_device.set_laser(
                laser_input=laser_input,
                enable=Enable.ENABLE,
                gpib_address=gpib,
                laser_type=LaserSource.LS_TunicsT100s_HP,  # Make configurable
                min_wavelength=min_wl,
                max_wavelength=max_wl,
                speed=speed,
            )
            logger.info(f"CT400 Connected (GPIB: {gpib}, Input: {laser_input.value}).")
            self._update_ct400_visuals(
                state="connected",
                message=f"CT400 Connected (Input {laser_input.value})",
            )
        except (CT400Error, ValueError, KeyError, Exception) as e:
            logger.error(f"CT400 connection failed: {e}", exc_info=True)
            self._update_ct400_visuals(state="error", message=f"Connection Failed: {e}")

    def _initiate_ct400_disconnection(self):
        self._update_ct400_visuals(
            state="disconnecting", message="CT400: Disconnecting..."
        )
        QApplication.processEvents()

        try:
            port_str_disconnect = self.config.get("ScanDefaults", {}).get(
                "input_port", "1"
            )  # Use a default or configured port for disconnect
            laser_input_disconnect = LaserInput(int(port_str_disconnect))
            self.ct400_device.cmd_laser(
                laser_input=laser_input_disconnect,
                enable=Enable.DISABLE,
                wavelength=1550.0,  # A typical safe wavelength
                power=1.0,  # Typically power is 0 for disable
            )
            logger.info("CT400 Disconnected.")
            self._update_ct400_visuals(
                state="disconnected", message="CT400 Disconnected"
            )
        except (CT400Error, ValueError, KeyError, Exception) as e:
            logger.error(f"Error during CT400 disconnection: {e}", exc_info=True)
            self._update_ct400_visuals(state="error", message=f"Disconnect Failed: {e}")

    def _show_about_dialog(self):
        app_name = self.config.get("App", {}).get("name", "Lab Control")
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
            logger.error("Camera container or layout not found.")
            return
        if not self.cameras_menu:
            logger.error(
                "Cameras menu not initialized. Cannot add camera control actions."
            )
            return

        self._cleanup_cameras()  # Clear previous camera widgets and menu actions
        camera_layout = self.camera_container.layout()

        cameras_initialized_count = 0
        for section_name in self.config.keys():
            if section_name.lower().startswith("camera:"):
                cam_config = self.config[section_name]
                is_enabled = str(cam_config.get("enabled", "false")).lower() == "true"
                if not is_enabled:
                    logger.info(f"Skipping disabled camera: {section_name}")
                    continue

                identifier = cam_config.get("identifier")
                name = cam_config.get(
                    "name", section_name
                )  # This is panel._panel_title
                flip = str(cam_config.get("flip_horizontal", "false")).lower() == "true"
                if not identifier or identifier.startswith("PUT_"):
                    logger.warning(f"Skipping camera '{name}': Invalid identifier.")
                    continue

                logger.info(
                    f"Attempting to initialize camera: {name} (ID: {identifier})"
                )
                try:
                    cam = VimbaCam(
                        identifier=identifier,
                        camera_name=name,
                        flip_horizontal=flip,
                        parent=self,
                    )
                    if not cam.open():
                        logger.error(f"Failed to open camera {name}. Skipping panel.")
                        cam.close()
                        continue

                    panel = CameraPanel(
                        cam, name, config=cam_config, parent=self.camera_container
                    )
                    camera_layout.addWidget(panel)
                    self.cameras.append(cam)
                    self.camera_panels[identifier] = panel

                    cam.new_frame.connect(panel.process_new_frame_data)
                    if hasattr(panel, "update_fps"):
                        cam.fps_updated.connect(panel.update_fps)

                    # Create menu action for this camera panel's controls
                    action = QAction(self)  # Text set below
                    action.setCheckable(True)
                    # panel.get_controls_visible() should be False initially by CameraPanel's default
                    action.setChecked(panel.get_controls_visible())
                    action.setText(
                        f"{'Hide' if panel.get_controls_visible() else 'Show'} {name} Controls"
                    )
                    action.setData(identifier)  # Store camera identifier
                    action.triggered.connect(self._handle_camera_control_toggle)

                    self.cameras_menu.addAction(action)
                    self.camera_control_actions[identifier] = action

                    logger.info(
                        f"Successfully initialized and connected signals for camera: {name}"
                    )
                    cameras_initialized_count += 1
                except VmbCameraError as e:
                    logger.error(f"Vimba Error initializing camera {name}: {e}")
                    QMessageBox.warning(
                        self, "Camera Error", f"Vimba error for {name}:\n{e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Unexpected error initializing camera {name}: {e}",
                        exc_info=True,
                    )
                    QMessageBox.critical(
                        self, "Camera Error", f"Critical error for {name}:\n{e}"
                    )

        if cameras_initialized_count == 0:
            logger.warning("No cameras were successfully initialized.")
            placeholder_label = QLabel("No cameras available or configured.")
            placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            camera_layout.addWidget(placeholder_label)
            if self.cameras_menu:  # If no cameras, add a disabled placeholder
                no_cam_action = QAction("No cameras available", self)
                no_cam_action.setEnabled(False)
                self.cameras_menu.addAction(no_cam_action)

        else:
            logger.info(f"Total cameras initialized: {cameras_initialized_count}")

    @Slot(bool)
    def _handle_camera_control_toggle(self, checked: bool):
        action = self.sender()
        if not isinstance(action, QAction):
            return

        camera_identifier = action.data()
        if camera_identifier in self.camera_panels:
            panel = self.camera_panels[camera_identifier]
            panel.set_controls_visibility(checked)
            # panel._panel_title is the same as 'name' used when creating the action
            action.setText(
                f"{'Hide' if checked else 'Show'} {panel._panel_title} Controls"
            )
        else:
            logger.warning(
                f"Camera panel not found for identifier: {camera_identifier} during toggle."
            )
            action.setChecked(not checked)  # Revert UI if panel not found
            action.setEnabled(False)  # Disable action if problematic

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

        # Remove camera control actions from menu and dict
        if hasattr(self, "cameras_menu") and self.cameras_menu is not None:
            for cam_id in list(
                self.camera_control_actions.keys()
            ):  # Iterate copy of keys
                action = self.camera_control_actions.pop(cam_id, None)
                if action:
                    self.cameras_menu.removeAction(action)
                    action.deleteLater()
        self.camera_control_actions.clear()

        # Clear panels from layout first
        if (
            hasattr(self, "camera_container")
            and self.camera_container.layout() is not None
        ):
            layout = self.camera_container.layout()
            while layout.count():  # Remove all widgets from layout
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)  # Remove from layout
                    widget.deleteLater()  # Schedule for deletion

        cameras_to_close = list(self.cameras)  # Iterate a copy
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
        if hasattr(self, "histogram_control") and self.histogram_control.monitoring:
            logger.debug("Stopping histogram monitoring...")
            self.histogram_control._stop_monitoring()
        if hasattr(self, "control_panel") and self.control_panel.scanning:
            logger.debug("Requesting scan stop via control panel...")
            self.control_panel._stop_scan(cancelled=True)

        self._cleanup_cameras()

        if self.ct400_device:
            logger.debug("Closing central CT400 device...")
            try:
                if self.is_ct400_connected_state:
                    try:
                        port_str_disconnect = self.config.get("ScanDefaults", {}).get(
                            "input_port", "1"
                        )
                        laser_input_disconnect = LaserInput(int(port_str_disconnect))
                        self.ct400_device.cmd_laser(
                            laser_input_disconnect,
                            Enable.DISABLE,
                            1550.0,
                            1.0,  # power 1.0
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
        # self.cleanup() # Cleanup is now handled by app.aboutToQuit
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


if __name__ == "__main__":
    import sys

    test_config = {
        "Logging": {"level": "DEBUG", "file": "main_window_test.log"},
        "Instruments": {
            "ct400_dll_path": "path/to/your/CT400_lib.dll"  # IMPORTANT: Update this path
        },
        "Camera:TopCam": {  # Example Camera
            "enabled": "true",
            "identifier": "DEV_000F31024699",  # Example, replace with your camera ID
            "name": "Top View Camera",
            "flip_horizontal": "false",
        },
        "Camera:SideCam": {  # Example Camera
            "enabled": "true",
            "identifier": "DEV_000F3102469A",  # Example, replace with your camera ID
            "name": "Side View Camera",
            "flip_horizontal": "true",
        },
        "ScanDefaults": {
            "start_wavelength_nm": "1550.0",
            "end_wavelength_nm": "1560.0",
            "resolution_pm": "1",
            "speed_nm_s": "10",
            "laser_power": "1.0",
            "power_unit": "mW",
            "input_port": "1",
            "min_wavelength_nm": "1440",
            "max_wavelength_nm": "1640",
        },
        "HistogramDefaults": {
            "wavelength_nm": "1550",
            "laser_power": "1",
            "power_unit": "mW",
            "input_port": "1",
        },
        "App": {"name": "Test Lab Control"},
        "UI": {"initial_width_ratio": "0.7", "initial_height_ratio": "0.7"},
    }
    try:
        import configparser

        cfg_parser = configparser.ConfigParser()
        # Try to find config.ini in the script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file_path = os.path.join(script_dir, "config.ini")

        if os.path.exists(config_file_path):
            cfg_parser.read(config_file_path)
            loaded_config = {}
            for section in cfg_parser.sections():
                loaded_config[section] = {}
                for key, value in cfg_parser.items(section):
                    if value.lower() in ["true", "yes", "on"]:
                        loaded_config[section][key] = True
                    elif value.lower() in ["false", "no", "off"]:
                        loaded_config[section][key] = False
                    elif value.isdigit():
                        loaded_config[section][key] = int(value)
                    else:
                        try:
                            loaded_config[section][key] = float(value)
                        except ValueError:
                            loaded_config[section][key] = value
            test_config = loaded_config
            print(f"Loaded {config_file_path} for testing.")
        else:
            print(
                f"{config_file_path} not found, using dummy config. Update ct400_dll_path and camera IDs."
            )
    except Exception as e:
        print(f"Error loading config.ini for test: {e}")

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s %(levelname)s:%(name)s:%(message)s"
    )
    # If using styles.py, ensure it's available and uncomment:
    # try:
    #     from styles import APP_STYLESHEET, apply_global_styles
    # except ImportError:
    #     APP_STYLESHEET = "" # Default empty stylesheet
    #     def apply_global_styles(app): pass
    #     logging.warning("styles.py not found or APP_STYLESHEET/apply_global_styles not defined.")

    app = QApplication(sys.argv)
    # if APP_STYLESHEET:
    #     app.setStyleSheet(APP_STYLESHEET)
    # apply_global_styles(app) # If you have a function for more complex styling

    window = MainWindow(config=test_config)
    window.show()

    # Connect cleanup to application's aboutToQuit signal
    app.aboutToQuit.connect(window.cleanup)

    sys.exit(app.exec())
