import logging
import os
import sys
from enum import Enum, auto

import numpy as np
from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QSize, Qt, QThread, QTimer, Slot
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

from config_model import AppConfig, CameraConfig
from hardware.dummy_ct400 import DummyCT400

try:
    import resources.resources_rc as resources_rc  # noqa: F401
except ImportError:
    print(
        "Warning: Compiled resource file (resources_rc.py) not found. Icons might be missing.",
        file=sys.stderr,
    )

from hardware.camera import VimbaCam
from hardware.camera_init_worker import CameraInitWorker
from hardware.ct400 import CT400, CT400Error
from hardware.ct400_types import Enable, LaserInput
from hardware.interfaces import AbstractCT400
from ui.camera_widgets import CameraPanel
from ui.constants import ID_CT400_STATUS_LABEL, PROP_STATUS
from ui.control_panel import CT400ConnectionWorker, CT400ControlPanel, HistogramControlPanel, ScanSettings
from ui.plot_widgets import HistogramWidget, PlotWidget

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
        self.cameras: list[VimbaCam] = []
        self.camera_panels: dict[str, CameraPanel] = {}
        self.ct400_device: AbstractCT400 | None = None
        self.shared_scan_settings = ScanSettings()
        self.vmb_instance: VmbSystem | None = None
        self.is_ct400_connected_state = False
        self.camera_control_actions: dict[str, QAction] = {}
        self.cameras_menu: QMenu | None = None

        # --- FIX: Add a list to hold worker references ---
        self.camera_init_threads: list[QThread] = []
        self.camera_init_workers: list[CameraInitWorker] = []

        self.ct400_connection_thread: QThread | None = None
        self.ct400_connection_worker: CT400ConnectionWorker | None = None

        # --- DEFERRED INITIALIZATION ---
        # Only build the UI in the constructor. Hardware init is deferred.
        self._init_ui()
        self._load_defaults_from_config()
        self._connect_signals()

        # Trigger slow hardware initializations AFTER the UI is constructed and shown.
        QTimer.singleShot(100, self._begin_lazy_init)

        logger.info("MainWindow __init__ complete. Hardware initialization deferred.")

    def _begin_lazy_init(self):
        """Starts all slow hardware initializations in the background."""
        logger.info("Starting lazy initialization of hardware...")
        self._start_vimbasystem()
        self._init_instruments()

        # --- FIX: Pass the live instrument object to the control panels ---
        if self.ct400_device:
            self.control_panel.set_instrument(self.ct400_device)
            self.histogram_control.set_instrument(self.ct400_device)

        if self.ct400_device and not isinstance(self.ct400_device, DummyCT400):  # Assuming DummyCT400 exists
            self._update_ct400_visuals(state=CT400Status.DISCONNECTED, message="CT400 Ready (Disconnected)")

        # Update UI based on instrument init before starting cameras
        self.control_panel.on_instrument_connected(self.ct400_device is not None)
        self.histogram_control.on_instrument_connected(self.ct400_device is not None)
        if self.ct400_device:
            self._update_ct400_visuals(state=CT400Status.DISCONNECTED, message="CT400 Ready (Disconnected)")

        # Now that Vimba is running, start initializing cameras
        if self.vmb_instance:
            self._init_cameras_lazy()
        else:
            logger.error("VimbaSystem not active, skipping camera initialization.")
            if self.camera_container.layout():
                error_label = QLabel("Vimba API could not be initialized. Cameras unavailable.")
                error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.camera_container.layout().addWidget(error_label)

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
                logger.critical(f"Unexpected error exiting VmbSystem: {e}", exc_info=True)
            finally:
                self.vmb_instance = None

    def _load_defaults_from_config(self):
        """Load default values from config into UI elements."""
        logger.debug("Loading UI defaults from configuration...")
        try:
            if hasattr(self, "control_panel") and self.control_panel:
                scan_defaults = self.config.scan_defaults
                self.control_panel.initial_wl.setText(str(scan_defaults.start_wavelength_nm))
                self.control_panel.final_wl.setText(str(scan_defaults.end_wavelength_nm))
                self.control_panel.resolution.setText(str(scan_defaults.resolution_pm))
                self.control_panel.motor_speed.setText(str(scan_defaults.speed_nm_s))
                self.control_panel.laser_power.setText(str(scan_defaults.laser_power))
                power_unit_idx = self.control_panel.power_unit.findText(scan_defaults.power_unit)
                if power_unit_idx != -1:
                    self.control_panel.power_unit.setCurrentIndex(power_unit_idx)
                for i in range(self.control_panel.input_port.count()):
                    if self.control_panel.input_port.itemData(i).value == scan_defaults.input_port:
                        self.control_panel.input_port.setCurrentIndex(i)
                        break

            if hasattr(self, "histogram_control") and self.histogram_control:
                hist_defaults = self.config.histogram_defaults
                self.histogram_control.wavelength_input.setText(str(hist_defaults.wavelength_nm))
                self.histogram_control.laser_power.setText(str(hist_defaults.laser_power))
                power_unit_idx_hist = self.histogram_control.power_unit.findText(hist_defaults.power_unit)
                if power_unit_idx_hist != -1:
                    self.histogram_control.power_unit.setCurrentIndex(power_unit_idx_hist)
                for i in range(self.histogram_control.input_port.count()):
                    if self.histogram_control.input_port.itemData(i).value == hist_defaults.input_port:
                        self.histogram_control.input_port.setCurrentIndex(i)
                        break
                if hasattr(self.histogram_control, "detector_cbs"):
                    for i, checkbox in enumerate(self.histogram_control.detector_cbs):
                        is_enabled = getattr(hist_defaults, f"detector_{i + 1}_enabled", False)
                        checkbox.setChecked(is_enabled)
        except Exception as e:
            logger.error(f"Error loading defaults from config: {e}", exc_info=True)
            self.statusBar.showMessage("Error loading defaults from config", 5000)

    def _init_instruments(self):
        logger.info("Initializing instruments...")
        from hardware.ct400 import CT400
        from hardware.dummy_ct400 import DummyCT400

        dll_path = self.config.instruments.ct400_dll_path
        self.ct400_device = None

        if not dll_path or not os.path.exists(dll_path):
            msg = f"CT400 DLL not found or path invalid: '{dll_path}'. Using dummy device."
            logger.warning(msg)
            self.ct400_device = DummyCT400()
            self._update_ct400_visuals(state=CT400Status.UNAVAILABLE, message=msg)
            return

        try:
            self.ct400_device = CT400(dll_path)
            logger.info(f"CT400 device object created using DLL: {dll_path}")
        except (CT400Error, FileNotFoundError, OSError) as e:
            logger.error(f"Failed to initialize CT400 device: {e}", exc_info=True)
            self.ct400_device = DummyCT400()
            self._update_ct400_visuals(state=CT400Status.UNAVAILABLE, message=f"CT400 Init Failed: {e}")
        except Exception as e:
            logger.critical(f"Unexpected error during CT400 initialization: {e}", exc_info=True)
            self.ct400_device = DummyCT400()
            self._update_ct400_visuals(state=CT400Status.UNAVAILABLE, message="CT400 Critical Error")

    def _init_ui(self):
        logger.debug("Initializing UI...")
        self.setWindowTitle(self.config.app_name)
        self.setWindowIcon(QIcon(":/icons/laser.svg"))
        self.setMinimumSize(1200, 800)

        try:
            screen = QApplication.primaryScreen().availableGeometry()
            width_ratio = self.config.ui.initial_width_ratio
            height_ratio = self.config.ui.initial_height_ratio
            self.resize(int(screen.width() * width_ratio), int(screen.height() * height_ratio))
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
        self.tab_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        control_layout.addWidget(self.tab_widget)
        splitter.addWidget(self.camera_container)
        splitter.addWidget(control_container)
        initial_cam_height = int(self.height() * 0.6)
        splitter.setSizes([initial_cam_height, self.height() - initial_cam_height])
        main_layout.addWidget(splitter)

        # --- IMPORTANT: Pass None for ct400_device initially ---
        self.first_tab = QWidget()
        first_tab_layout = QHBoxLayout(self.first_tab)
        self.control_panel = CT400ControlPanel(self.shared_scan_settings, None, self.config)
        self.plot_widget = PlotWidget(self.shared_scan_settings)
        first_tab_layout.addWidget(self.control_panel, stretch=0)
        first_tab_layout.addWidget(self.plot_widget, stretch=1)
        self.tab_widget.addTab(self.first_tab, "Wavelength Scan")

        self.second_tab = QWidget()
        second_tab_layout = QHBoxLayout(self.second_tab)
        self.histogram_control = HistogramControlPanel(None, self.config)
        hist_detector_keys = [cb.text() for cb in self.histogram_control.detector_cbs]
        self.histogram_widget = HistogramWidget(self.histogram_control, hist_detector_keys)
        second_tab_layout.addWidget(self.histogram_control, stretch=0)
        second_tab_layout.addWidget(self.histogram_widget, stretch=1)
        self.tab_widget.addTab(self.second_tab, "Power Monitor")

        self.setStatusBar(QStatusBar())
        self.ct400_status_label = QLabel("CT400: Initializing...")
        self.ct400_status_label.setObjectName(ID_CT400_STATUS_LABEL)
        self.statusBar().addPermanentWidget(self.ct400_status_label)
        self.statusBar().showMessage("Ready.")

        self._create_menus()
        self._update_ct400_visuals(state=CT400Status.UNKNOWN, message="Initializing...")
        logger.debug("UI Initialization finished.")

    def _update_ct400_visuals(self, state: CT400Status, message: str | None = None):
        action = getattr(self, "ct400_connect_action", None)
        label = getattr(self, "ct400_status_label", None)
        if not action or not label:
            return

        action_enabled, action_checked = True, False
        status_property = state.name.lower()

        state_map = {
            CT400Status.CONNECTED: ("Disconnect CT400", ":/icons/disconnect.svg", True, True),
            CT400Status.DISCONNECTED: ("Connect CT400", ":/icons/connect.svg", True, False),
            CT400Status.CONNECTING: ("Connecting...", ":/icons/spinner.svg", False, True),
            CT400Status.DISCONNECTING: ("Disconnecting...", ":/icons/spinner.svg", False, False),
            CT400Status.ERROR: ("Connect CT400 (Error)", ":/icons/connect.svg", True, False),
            CT400Status.UNAVAILABLE: ("CT400 Unavailable", ":/icons/laser.svg", False, False),
            CT400Status.UNKNOWN: ("CT400 Initializing", ":/icons/spinner.svg", False, False),
        }

        text, icon, action_enabled, action_checked = state_map[state]
        action.setText(text)
        action.setIcon(QIcon(icon))
        action.setEnabled(action_enabled and isinstance(self.ct400_device, CT400))
        action.setChecked(action_checked)

        label.setText(f"CT400: {state.name.replace('_', ' ').title()}")
        label.setProperty(PROP_STATUS, status_property)
        label.style().unpolish(label)
        label.style().polish(label)

        if message:
            timeout = 5000 if state in [CT400Status.CONNECTED, CT400Status.DISCONNECTED, CT400Status.ERROR] else 0
            self.statusBar().showMessage(message, timeout)

        if state == CT400Status.ERROR:
            QTimer.singleShot(
                3000, lambda: self._update_ct400_visuals(CT400Status.DISCONNECTED, "Error occurred. Ready to connect.")
            )

        self.is_ct400_connected_state = state == CT400Status.CONNECTED

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
        self.ct400_connect_action.triggered.connect(self._handle_ct400_connect_action_triggered)
        self.instrument_menu.addAction(self.ct400_connect_action)

        self.cameras_menu = menu_bar.addMenu("&Cameras")

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.setStatusTip("Show application information")
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)
        logger.debug("Menus created.")

    @Slot(bool)
    def _handle_ct400_connect_action_triggered(self, checked: bool):
        """
        Handles the triggered signal from the QAction by starting a worker thread
        to perform the connect or disconnect operation.
        """
        if self.ct400_connection_thread and self.ct400_connection_thread.isRunning():
            logger.warning("CT400 connection/disconnection already in progress.")
            # Revert the action's visual state because the action was premature
            self.ct400_connect_action.setChecked(not checked)
            return

        if not self.ct400_device or isinstance(self.ct400_device, DummyCT400):
            self._update_ct400_visuals(state=CT400Status.UNAVAILABLE, message="CT400 Not Initialized or Dummy.")
            self.ct400_connect_action.setChecked(False)
            return

        # Import the worker class here to avoid circular dependency at module level
        from ui.control_panel import CT400ConnectionWorker

        self.ct400_connection_thread = QThread(self)
        self.ct400_connection_worker = CT400ConnectionWorker(self.ct400_device, self.config)
        self.ct400_connection_worker.moveToThread(self.ct400_connection_thread)

        # --- THE CRITICAL FIX IS HERE ---
        # 1. When the worker finishes its job, it tells the thread to quit.
        self.ct400_connection_worker.finished.connect(self.ct400_connection_thread.quit)

        # Connect worker result signals to main window slots
        self.ct400_connection_worker.connection_succeeded.connect(self._handle_ct400_connection_success)
        self.ct400_connection_worker.connection_failed.connect(self._handle_ct400_connection_failure)
        self.ct400_connection_worker.disconnection_succeeded.connect(self._handle_ct400_disconnection_success)
        self.ct400_connection_worker.disconnection_failed.connect(self._handle_ct400_connection_failure)

        # 2. When the thread has fully finished (after quitting), clean everything up.
        self.ct400_connection_thread.finished.connect(self.ct400_connection_worker.deleteLater)
        self.ct400_connection_thread.finished.connect(self.ct400_connection_thread.deleteLater)
        self.ct400_connection_thread.finished.connect(self._connection_thread_finished)

        if checked:
            # Start connection
            self._update_ct400_visuals(state=CT400Status.CONNECTING, message="CT400: Attempting to connect...")
            self.ct400_connection_thread.started.connect(self.ct400_connection_worker.connect_device)
        else:
            # Start disconnection
            self._update_ct400_visuals(state=CT400Status.DISCONNECTING, message="CT400: Disconnecting...")
            self.ct400_connection_thread.started.connect(self.ct400_connection_worker.disconnect_device)

        self.ct400_connection_thread.start()

    @Slot(str)
    def _handle_ct400_connection_success(self, message: str):
        self._update_ct400_visuals(state=CT400Status.CONNECTED, message=message)
        self.control_panel.on_instrument_connected(True)
        self.histogram_control.on_instrument_connected(True)

    @Slot(str)
    def _handle_ct400_connection_failure(self, error_message: str):
        self._update_ct400_visuals(state=CT400Status.ERROR, message=error_message)
        self.control_panel.on_instrument_connected(False)
        self.histogram_control.on_instrument_connected(False)

    @Slot(str)
    def _handle_ct400_disconnection_success(self, message: str):
        self._update_ct400_visuals(state=CT400Status.DISCONNECTED, message=message)
        self.control_panel.on_instrument_connected(False)
        self.histogram_control.on_instrument_connected(False)

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

    def _init_cameras_lazy(self):
        """
        Initializes cameras asynchronously.
        For each valid camera in the config, this method creates a placeholder UI panel,
        then spawns a dedicated worker thread to open the camera.
        """
        logger.info("Initializing cameras lazily...")
        if not all([self.camera_container, self.camera_container.layout(), self.cameras_menu]):
            logger.error("UI components for camera initialization are not ready. Aborting.")
            return

        # Get a dictionary of valid camera configs, keyed by their identifier.
        camera_configs_to_init = {
            identifier: config
            for identifier, config in self.config.cameras.items()
            if self._should_initialize_camera(identifier, config)
        }

        if not camera_configs_to_init:
            self.cameras_menu.addAction(QAction("No enabled cameras found in config", self)).setEnabled(False)
            logger.info("No valid, enabled cameras to initialize.")
            return

        # --- COMBINED LOOP: Create panel and start worker thread in one pass ---
        for identifier, config in camera_configs_to_init.items():
            logger.debug(f"Setting up initialization for camera '{config.name}' (ID: {identifier})")

            # 1. Create and add the placeholder panel to the UI
            placeholder_panel = self._create_camera_panel(None, config)
            self.camera_panels[identifier] = placeholder_panel
            self.camera_container.layout().addWidget(placeholder_panel)

            # 2. Create the thread and worker for this specific camera
            thread = QThread(self)
            worker = CameraInitWorker(identifier=identifier, cam_config=config)
            worker.moveToThread(thread)

            # 3. Connect signals for this worker/thread instance
            worker.camera_initialized.connect(self._on_camera_initialized)
            worker.camera_initialized.connect(thread.quit)  # Tell thread to quit when done

            # 4. Connect cleanup signals
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            # Remove the thread from our tracking list upon completion
            thread.finished.connect(
                lambda t=thread: self.camera_init_threads.remove(t) if t in self.camera_init_threads else None
            )

            # 5. Start the thread
            thread.started.connect(worker.run)
            thread.start()

            # 6. Keep a persistent reference to prevent garbage collection
            self.camera_init_threads.append(thread)
            self.camera_init_workers.append(worker)

    @Slot(str, object, CameraConfig)
    def _on_camera_initialized(self, identifier: str, camera_instance: VimbaCam | None, cam_config: CameraConfig):
        """Slot to handle a camera that has finished initializing."""
        panel = self.camera_panels.get(identifier)  # Find panel by identifier
        if not panel:
            logger.error(f"Could not find panel for camera config: {cam_config.name}")
            if camera_instance:
                camera_instance.close()
            return

        if camera_instance:
            logger.info(f"Camera '{cam_config.name}' is online. Connecting to UI.")
            self.cameras.append(camera_instance)

            panel.set_camera(camera_instance)
            self._connect_camera_signals(camera_instance, panel)
            self._create_camera_menu_action(camera_instance, panel)
            panel.watchdog_timer.start()

            # --- CRITICAL FIX: Manually process the latest available frame ---
            # The camera has been streaming in the background. Its internal buffer
            # likely holds a recent frame. We pull it and update the UI immediately.
            latest_frame = camera_instance.get_latest_frame()
            if latest_frame is not None:
                logger.debug(f"Manually processing first frame for {camera_instance.camera_name}")
                panel.process_new_frame_data(latest_frame)
            else:
                # If no frame is available yet, update the text.
                panel.video_label.setText("Waiting for frames...")
        else:
            logger.error(f"Failed to initialize camera '{cam_config.name}'.")
            error_msg = f"{cam_config.name}\n(Failed to Open)"
            panel.video_label.setText(error_msg)
            panel.video_label.setStyleSheet("background-color: #ffebee; color: #c62828;")

    def _should_initialize_camera(self, identifier: str, cam_config: "CameraConfig") -> bool:
        """Checks if a camera from the config should be initialized."""
        if not cam_config.enabled:
            logger.info(f"Skipping disabled camera: {cam_config.name}")
            return False
        if not identifier or identifier.startswith("PUT_"):
            logger.warning(f"Skipping camera '{cam_config.name}': Invalid or placeholder identifier.")
            error_msg = f"{cam_config.name}\n(Config Error: Invalid ID)"
            placeholder = self._create_camera_error_placeholder(error_msg)
            self.camera_container.layout().addWidget(placeholder)
            return False
        return True

    def _create_and_open_camera(self, cam_config: "CameraConfig") -> VimbaCam | None:
        """Creates a VimbaCam instance and attempts to open it. Returns instance or None."""
        try:
            cam_instance = VimbaCam(
                identifier=cam_config.identifier,
                camera_name=cam_config.name,
                flip_horizontal=cam_config.flip_horizontal,
                parent=self,
            )
            if not cam_instance.open():
                logger.error(f"Failed to open camera {cam_config.name} (ID: {cam_config.identifier}).")
                error_msg = f"{cam_config.name}\n(Failed to Open)"
                placeholder = self._create_camera_error_placeholder(error_msg)
                self.camera_container.layout().addWidget(placeholder)
                cam_instance.close()  # Clean up the failed instance
                return None
            return cam_instance
        except VmbCameraError as e:
            logger.error(f"Vimba Error initializing camera {cam_config.name} (ID: {cam_config.identifier}): {e}")
            error_msg = f"{cam_config.name}\n(Vimba Error)"
            placeholder = self._create_camera_error_placeholder(error_msg)
            self.camera_container.layout().addWidget(placeholder)
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error creating camera instance {cam_config.name}: {e}",
                exc_info=True,
            )
            error_msg = f"{cam_config.name}\n(Init Error)"
            placeholder = self._create_camera_error_placeholder(error_msg)
            self.camera_container.layout().addWidget(placeholder)
            return None

    def _create_camera_panel(self, cam_instance: VimbaCam | None, cam_config: "CameraConfig") -> CameraPanel:
        """Creates and returns a CameraPanel. Can be a placeholder if cam_instance is None."""
        panel = CameraPanel(cam_instance, cam_config.name, config=cam_config, parent=self.camera_container)
        if not cam_instance:
            panel.video_label.setText(f"Connecting to\n{cam_config.name}...")
        return panel

    def _connect_camera_signals(self, cam_instance: VimbaCam, panel: CameraPanel):
        """Connects signals between a camera instance and its UI panel."""
        cam_instance.new_frame.connect(panel.process_new_frame_data)
        cam_instance.fps_updated.connect(panel.update_fps)
        # Connect the camera's generic error signal to the panel for display
        cam_instance.error.connect(panel._handle_camera_error_message)

    def _create_camera_menu_action(self, cam_instance: VimbaCam, panel: CameraPanel):
        """Creates and registers a menu action to control the camera panel's visibility."""
        action = QAction(self)
        action.setCheckable(True)
        action.setChecked(panel.get_controls_visible())
        action.setText(f"{'Hide' if panel.get_controls_visible() else 'Show'} {cam_instance.camera_name} Controls")
        action.setData(cam_instance.identifier)
        action.triggered.connect(self._handle_camera_control_toggle)
        self.cameras_menu.addAction(action)
        self.camera_control_actions[cam_instance.identifier] = action

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
        placeholder_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(placeholder_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        icon_label = QLabel()
        try:
            std_icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
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

        placeholder_widget.setToolTip(f"Camera Initialization Error: {message.replace('\n', ' ')}")
        return placeholder_widget

    @Slot()
    def _connection_thread_finished(self):
        """Cleans up references to the connection thread and worker after it has finished."""
        logger.debug("CT400 connection thread has finished. Cleaning up references.")
        self.ct400_connection_thread = None
        self.ct400_connection_worker = None

    @Slot(bool)
    def _handle_camera_control_toggle(self, checked: bool):
        action = self.sender()
        if not isinstance(action, QAction):
            return

        camera_identifier = action.data()
        if camera_identifier in self.camera_panels:
            panel = self.camera_panels[camera_identifier]
            panel.set_controls_visibility(checked)
            action.setText(f"{'Hide' if checked else 'Show'} {panel._panel_title} Controls")
        else:
            logger.warning(f"Camera panel not found for identifier: {camera_identifier} during toggle.")
            action.setChecked(not checked)
            action.setEnabled(False)

    @Slot(np.ndarray, np.ndarray, float)
    def _handle_scan_data(
        self,
        wavelengths: np.ndarray,
        plotting_power_data: np.ndarray,
        final_pout: float,
    ):
        logger.debug(f"Received scan data signal. Wavelength points: {len(wavelengths)}")

        if self.plot_widget and hasattr(self.plot_widget, "update_plot"):
            try:
                self.plot_widget.update_plot(wavelengths, plotting_power_data, final_pout)
            except Exception as e:
                logger.error(f"Error updating plot widget: {e}", exc_info=True)

    @Slot(dict)
    def handle_power_data(self, power_data: dict):
        logger.debug(f"Received power data: {power_data}")
        if self.histogram_widget and hasattr(self.histogram_widget, "schedule_update"):
            try:
                self.histogram_widget.schedule_update(power_data)
            except Exception as e:
                logger.error(f"Error updating histogram widget: {e}", exc_info=True)
        else:
            logger.warning("Histogram widget not available or does not have schedule_update method.")

    def _cleanup_cameras(self):
        logger.info(f"Closing {len(self.cameras)} camera(s)...")
        # --- FIX: The loop now only deals with threads that are *genuinely* still running ---
        # Make a copy of the list to iterate over, as the list itself might be modified
        threads_to_stop = list(self.camera_init_threads)
        for thread in threads_to_stop:
            if thread.isRunning():
                logger.warning("Force-quitting an incomplete camera init thread.")
                thread.quit()
                thread.wait(500)

        self.camera_init_threads.clear()
        self.camera_init_workers.clear()

        if hasattr(self, "cameras_menu") and self.cameras_menu is not None:
            for cam_id in list(self.camera_control_actions.keys()):
                action = self.camera_control_actions.pop(cam_id, None)
                if action:
                    self.cameras_menu.removeAction(action)
                    action.deleteLater()
        self.camera_control_actions.clear()

        if hasattr(self, "camera_container") and self.camera_container.layout() is not None:
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
                    logger.error(f"Error closing camera {cam.camera_name}: {e}", exc_info=True)
        logger.info("Camera cleanup finished.")

    def cleanup(self):
        logger.info("Performing MainWindow cleanup...")
        if hasattr(self, "histogram_control"):
            self.histogram_control.cleanup_worker_thread()
        if hasattr(self, "control_panel") and self.control_panel.scanning:
            self.control_panel._stop_scan(cancelled=True)
        if hasattr(self, "plot_widget"):
            self.plot_widget.cleanup()
        self._cleanup_cameras()
        if self.ct400_device:
            logger.debug("Closing central CT400 device...")
            try:
                if self.is_ct400_connected_state:
                    laser_input_disconnect = LaserInput(self.config.scan_defaults.input_port)
                    self.ct400_device.cmd_laser(laser_input_disconnect, Enable.DISABLE, 1550.0, 1.0)
                self.ct400_device.close()
                logger.info("CT400 device closed.")
            except Exception as e:
                logger.error(f"Error closing CT400 device: {e}", exc_info=True)
            self.ct400_device = None
        self._cleanup_vimbasystem()
        logger.info("MainWindow cleanup finished.")

    def closeEvent(self, event: QtGui.QCloseEvent):
        logger.info("Close event triggered for MainWindow.")
        # The actual cleanup is now handled by app.aboutToQuit
        event.accept()

    def _connect_signals(self):
        if hasattr(self, "control_panel") and self.control_panel:
            logger.debug("Connecting control_panel signals")
            self.control_panel.scan_data_ready.connect(self._handle_scan_data)
            # --- FIX: Add parentheses to self.statusBar() ---
            self.control_panel.progress_updated.connect(
                lambda value: self.statusBar().showMessage(f"Scan Progress: {value}%", 1000 if value < 100 else 0)
            )
        else:
            logger.warning("CT400 Control Panel not initialized, skipping signal connection.")

        if hasattr(self, "histogram_control") and self.histogram_control:
            logger.debug("Connecting histogram_control signals")
            self.histogram_control.power_data_ready.connect(self.handle_power_data)
        else:
            logger.warning("Histogram Control Panel not initialized, skipping signal connection.")
