import logging

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hardware.alignment_worker import AlignmentSettings, AlignmentWorker, MappingSettings, SpiralSearchSettings
from hardware.interfaces import AbstractCT400
from hardware.piezo import PiezoController
from ui.plot_widgets import ColorBarWidget, Plot3DWidget

logger = logging.getLogger("LabApp.AlignmentPanel")


class AlignmentPanel(QWidget):
    start_alignment_requested = Signal(object)
    start_mapping_requested = Signal(object)
    start_spiral_alignment_requested = Signal(object, object)

    def __init__(
        self,
        ct400: AbstractCT400 | None,
        piezo_left: PiezoController | None,
        piezo_right: PiezoController | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        # --- MODIFICATION: Store hardware, but don't use it yet ---
        self.ct400 = ct400
        self.piezo_left = piezo_left
        self.piezo_right = piezo_right

        # --- MODIFICATION: These will be initialized later ---
        self.worker_thread: QThread | None = None
        self.alignment_worker: AlignmentWorker | None = None

        self._init_ui()
        # --- MODIFICATION: Don't initialize worker immediately ---
        # self._init_worker() # This is now called from set_hardware

        # --- MODIFICATION: Set initial state based on hardware availability ---
        self.set_hardware_ready(bool(ct400 and piezo_left and piezo_right))

    def _init_ui(self):
        """Initializes all UI elements to replicate the target design."""
        # Main layout of the panel: Controls on the left, Plot on the right
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(10)

        # --- Left Side: A single container for all controls ---
        controls_container = QWidget()
        controls_v_layout = QVBoxLayout(controls_container)
        controls_v_layout.setContentsMargins(0, 0, 0, 0)
        controls_v_layout.setSpacing(8)

        # --- Two-Column Layout Container ---
        columns_layout = QHBoxLayout()
        columns_layout.setContentsMargins(0, 0, 0, 0)

        # --- Left Column ---
        left_column_layout = QVBoxLayout()

        # Group 1.1: Laser Settings
        self.laser_group = QGroupBox("Laser Settings")
        laser_form = QFormLayout()
        laser_form.setSpacing(5)
        laser_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.wavelength_input = QLineEdit("1550.0")
        self.wavelength_input.setValidator(QDoubleValidator(1440.0, 1640.0, 3))
        laser_form.addRow("Wavelength (nm):", self.wavelength_input)
        self.power_input = QLineEdit("1.0")
        self.power_input.setValidator(QDoubleValidator(0.1, 50.0, 3))
        self.power_unit_combo = QComboBox()
        self.power_unit_combo.addItems(["mW", "dBm"])
        laser_form.addRow("Power:", self._create_hbox(self.power_input, self.power_unit_combo))
        self.input_port_combo = QComboBox()
        for i in range(1, 5):
            self.input_port_combo.addItem(f"Port {i}", userData=i)
        laser_form.addRow("Input Port:", self.input_port_combo)
        self.laser_group.setLayout(laser_form)
        left_column_layout.addWidget(self.laser_group)

        # Group 1.2: Alignment Settings
        self.align_group = QGroupBox("Alignment Settings")
        align_form = QFormLayout()
        align_form.setSpacing(5)
        align_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.iterations_spin = QSpinBox(minimum=1, maximum=20, value=2)
        align_form.addRow("Iterations:", self.iterations_spin)
        self.step_spin = QDoubleSpinBox(minimum=10, maximum=1000, value=100)
        align_form.addRow("Step (nm):", self.step_spin)
        self.avg_spin = QSpinBox(minimum=1, maximum=10, value=1)
        align_form.addRow("Avg. Samples/Pt:", self.avg_spin)
        self.spiral_radius_spin = QDoubleSpinBox(minimum=1.0, maximum=20.0, value=5.0, singleStep=0.5, suffix=" µm")
        align_form.addRow("Spiral Radius:", self.spiral_radius_spin)
        self.spiral_step_spin = QDoubleSpinBox(minimum=0.1, maximum=2.0, value=0.5, singleStep=0.1, suffix=" µm")
        align_form.addRow("Spiral Step:", self.spiral_step_spin)
        self.butt_coupling_cb = QCheckBox("Butt")
        self.top_coupling_cb = QCheckBox("Top")
        self.butt_coupling_cb.setChecked(True)
        self.butt_coupling_cb.toggled.connect(lambda checked: self.top_coupling_cb.setChecked(not checked))
        self.top_coupling_cb.toggled.connect(lambda checked: self.butt_coupling_cb.setChecked(not checked))
        align_form.addRow("Coupling:", self._create_hbox(self.butt_coupling_cb, self.top_coupling_cb))
        # Add the new button
        self.spiral_align_button = QPushButton("Start Spiral Alignment")
        self.spiral_align_button.setToolTip("Recommended: Performs a wide search then a fine alignment.")
        self.align_button = QPushButton("Start Fine-Tune Only")
        self.align_button.setToolTip("Runs only the fine-tuning alignment from the current position.")

        # Add both buttons to the form
        align_form.addRow(self.spiral_align_button)
        align_form.addRow(self.align_button)

        self.align_group.setLayout(align_form)
        left_column_layout.addWidget(self.align_group)
        left_column_layout.addStretch()

        # --- Right Column ---
        right_column_layout = QVBoxLayout()

        # Group 2.1: 3D Mapping Settings
        self.map_group = QGroupBox("3D Mapping Settings")
        map_form = QFormLayout()
        map_form.setSpacing(5)
        map_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.map_left_radio = QRadioButton("Left Stage")
        self.map_right_radio = QRadioButton("Right Stage")
        self.map_left_radio.setChecked(True)
        map_form.addRow("Target:", self._create_hbox(self.map_left_radio, self.map_right_radio))
        self.x_min_spin = QSpinBox(minimum=-5000, maximum=5000, value=-3000, singleStep=100)
        self.x_max_spin = QSpinBox(minimum=-5000, maximum=5000, value=3000, singleStep=100)
        map_form.addRow("X Range (nm):", self._create_hbox(self.x_min_spin, self.x_max_spin))
        self.y_min_spin = QSpinBox(minimum=-5000, maximum=5000, value=-3000, singleStep=100)
        self.y_max_spin = QSpinBox(minimum=-5000, maximum=5000, value=3000, singleStep=100)
        map_form.addRow("Y Range (nm):", self._create_hbox(self.y_min_spin, self.y_max_spin))
        self.x_step_spin = QSpinBox(minimum=10, maximum=1000, value=500, singleStep=10)
        self.y_step_spin = QSpinBox(minimum=10, maximum=1000, value=500, singleStep=10)
        map_form.addRow("Step X/Y (nm):", self._create_hbox(self.x_step_spin, self.y_step_spin))
        self.map_button = QPushButton("Generate 3D Map")
        self.map_button.setObjectName("mapButton")
        self.map_button.setCheckable(True)
        self.map_button.setMinimumHeight(35)
        map_form.addRow(self.map_button)
        self.map_progress = QProgressBar()
        self.map_progress.setVisible(False)
        self.map_progress.setTextVisible(True)
        self.map_progress.setFormat("%p%")
        map_form.addRow(self.map_progress)
        self.map_group.setLayout(map_form)
        right_column_layout.addWidget(self.map_group)

        # Group 2.2: Live Power
        self.power_group = QGroupBox("Live Status")
        power_box_layout = QVBoxLayout()

        # NEW: Status label
        self.status_label = QLabel("Status: Idle")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        power_box_layout.addWidget(self.status_label)

        self.power_label = QLabel("--.-- dBm")
        font = self.power_label.font()
        font.setPointSize(20)
        font.setBold(True)
        self.power_label.setFont(font)
        self.power_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.power_label.setMinimumHeight(60)
        power_box_layout.addWidget(self.power_label)

        # NEW: Power bar
        self.power_bar = QProgressBar()
        self.power_bar.setRange(-80, 0)  # Typical dBm range
        self.power_bar.setValue(-80)
        self.power_bar.setTextVisible(False)
        power_box_layout.addWidget(self.power_bar)

        self.power_group.setLayout(power_box_layout)
        right_column_layout.addWidget(self.power_group)

        right_column_layout.addStretch()

        # --- Assemble Columns ---
        columns_layout.addLayout(left_column_layout)
        columns_layout.addLayout(right_column_layout)
        controls_v_layout.addLayout(columns_layout)

        # --- Final Assembly ---
        main_layout.addWidget(controls_container)

        # Create the new plot and color bar widgets
        self.plot3d_widget = Plot3DWidget()
        self.colorbar_widget = ColorBarWidget()

        # Add them to the main layout
        main_layout.addWidget(self.plot3d_widget, stretch=1)  # Plot takes most space
        main_layout.addWidget(self.colorbar_widget)  # Color bar sits to the right

        # --- Connect signals to slots ---
        self.align_button.clicked.connect(self.toggle_alignment)
        self.map_button.clicked.connect(self.toggle_mapping)
        self.spiral_align_button.clicked.connect(self.toggle_spiral_alignment)
        self.align_button.clicked.connect(self.toggle_alignment)

        # --- NEW: Connect the plot's signal to the color bar's slot ---
        self.plot3d_widget.colormap_updated.connect(self.colorbar_widget.update_colormap)

    @Slot()
    def toggle_spiral_alignment(self):
        # This is a one-shot button, not checkable
        align_settings = AlignmentSettings(
            laser_wavelength_nm=float(self.wavelength_input.text()),
            laser_power=float(self.power_input.text()),
            power_unit=self.power_unit_combo.currentText(),
            input_port=self.input_port_combo.currentData(),
            iterations=self.iterations_spin.value(),
            step_nm=self.step_spin.value(),
            samples_per_point=self.avg_spin.value(),
            coupling_type="butt" if self.butt_coupling_cb.isChecked() else "top",
        )
        spiral_settings = SpiralSearchSettings(
            radius_um=self.spiral_radius_spin.value(),
            step_um=self.spiral_step_spin.value(),
        )

        self.disable_all_buttons()
        self.start_spiral_alignment_requested.emit(align_settings, spiral_settings)

    def disable_all_buttons(self):
        self.spiral_align_button.setEnabled(False)
        self.align_button.setEnabled(False)
        self.map_button.setEnabled(False)

    def _create_hbox(self, *widgets):
        """Helper to create a QHBoxLayout for a row, reducing boilerplate."""
        layout = QHBoxLayout()
        for widget in widgets:
            layout.addWidget(widget)
        return layout

    def set_hardware(
        self,
        ct400: AbstractCT400,
        piezo_left: PiezoController,
        piezo_right: PiezoController,
    ):
        """Assigns the live hardware objects and activates the panel."""
        logger.info("AlignmentPanel receiving live hardware instances.")
        self.ct400 = ct400
        self.piezo_left = piezo_left
        self.piezo_right = piezo_right

        self._setup_worker_and_connections()
        self.set_hardware_ready(True)

    def set_hardware_ready(self, is_ready: bool):
        """Enables or disables the panel's controls."""
        # Now we disable/enable all the individual group boxes
        self.laser_group.setEnabled(is_ready)
        self.align_group.setEnabled(is_ready)
        self.map_group.setEnabled(is_ready)
        self.power_group.setEnabled(is_ready)  # Also control the power readout box

        if not is_ready:
            self.setToolTip("Hardware for alignment is not yet available or failed to initialize.")
        else:
            self.setToolTip("")

    def _setup_worker_and_connections(self):
        """Creates the worker and thread, and connects signals."""
        if self.worker_thread is not None:
            # Already initialized
            return

        if not all([self.ct400, self.piezo_left, self.piezo_right]):
            logger.error("Cannot setup worker, hardware dependencies are missing.")
            return

        self.worker_thread = QThread(self)
        self.alignment_worker = AlignmentWorker(self.ct400, self.piezo_left, self.piezo_right)
        self.alignment_worker.moveToThread(self.worker_thread)

        # Connect signals
        self.start_alignment_requested.connect(self.alignment_worker.run_alignment)
        self.alignment_worker.progress_updated.connect(self.on_progress_update)
        self.alignment_worker.alignment_finished.connect(self.on_alignment_finished)
        self.alignment_worker.error_occurred.connect(self.on_worker_error)
        self.start_mapping_requested.connect(self.alignment_worker.run_mapping)
        self.start_spiral_alignment_requested.connect(self.alignment_worker.run_spiral_alignment)
        self.alignment_worker.mapping_progress.connect(self.on_mapping_progress)
        self.alignment_worker.mapping_finished.connect(self.on_mapping_finished)

        self.worker_thread.start()
        logger.info("Alignment worker and thread started successfully.")

    @Slot(bool)
    def toggle_alignment(self, checked):
        if checked:
            try:
                settings = AlignmentSettings(
                    # NEW: Gather laser settings from the new UI elements
                    laser_wavelength_nm=float(self.wavelength_input.text()),
                    laser_power=float(self.power_input.text()),
                    power_unit=self.power_unit_combo.currentText(),
                    input_port=self.input_port_combo.currentData(),
                    # Existing algorithm settings
                    iterations=self.iterations_spin.value(),
                    step_nm=self.step_spin.value(),
                    samples_per_point=self.avg_spin.value(),
                    coupling_type="butt" if self.butt_coupling_cb.isChecked() else "top",
                    settling_time_ms=100,
                )
            except (ValueError, TypeError) as e:
                QMessageBox.critical(self, "Invalid Input", f"Please check laser settings. Error: {e}")
                self.align_button.setChecked(False)
                return

            self.align_button.setText("Stop Alignment")
            self.align_button.setProperty("running", True)
            self.start_alignment_requested.emit(settings)
        else:
            self.alignment_worker.stop()
            self.align_button.setText("Start AutoAlignment")
            self.align_button.setProperty("running", False)

        # Refresh the style
        self.align_button.style().unpolish(self.align_button)
        self.align_button.style().polish(self.align_button)

    @Slot(bool)
    def toggle_mapping(self, checked):
        """Starts or stops the power mapping process."""
        if checked:
            self.map_button.setText("Stop Map")
            self.map_button.setProperty("running", True)
            self.map_progress.setValue(0)
            self.map_progress.setVisible(True)
            try:
                settings = MappingSettings(
                    # NEW: Gather laser settings
                    laser_wavelength_nm=float(self.wavelength_input.text()),
                    laser_power=float(self.power_input.text()),
                    power_unit=self.power_unit_combo.currentText(),
                    input_port=self.input_port_combo.currentData(),
                    # Existing mapping settings
                    x_min_nm=self.x_min_spin.value(),
                    x_max_nm=self.x_max_spin.value(),
                    x_step_nm=self.x_step_spin.value(),
                    y_min_nm=self.y_min_spin.value(),
                    y_max_nm=self.y_max_spin.value(),
                    y_step_nm=self.y_step_spin.value(),
                    samples_per_point=self.avg_spin.value(),
                    stage_to_map="left" if self.map_left_radio.isChecked() else "right",
                    settling_time_ms=100,
                )
            except (ValueError, TypeError) as e:
                QMessageBox.critical(self, "Invalid Input", f"Please check laser settings. Error: {e}")
                self.map_button.setChecked(False)
                self.map_progress.setVisible(False)
                return

            self.start_mapping_requested.emit(settings)
        else:
            self.alignment_worker.stop()
            self.map_button.setText("Do Map")
            self.map_button.setProperty("running", False)
            self.map_progress.setVisible(False)

        # Refresh the style
        self.map_button.style().unpolish(self.map_button)
        self.map_button.style().polish(self.map_button)

    def reset_buttons(self):
        """Helper to re-enable all action buttons to their idle state."""
        # Reset Spiral Alignment Button
        self.spiral_align_button.setEnabled(True)

        # Reset Fine-Tune Button
        self.align_button.setChecked(False)
        self.align_button.setText("Start Fine-Tune Only")
        self.align_button.setProperty("running", False)
        self.align_button.style().unpolish(self.align_button)
        self.align_button.style().polish(self.align_button)
        self.align_button.setEnabled(True)

        # Reset Mapping Button
        self.map_button.setChecked(False)
        self.map_button.setText("Generate 3D Map")
        self.map_button.setProperty("running", False)
        self.map_button.style().unpolish(self.map_button)
        self.map_button.style().polish(self.map_button)
        self.map_button.setEnabled(True)

    @Slot(str, float, bool)
    def on_progress_update(self, message: str, power: float, is_final_for_axis: bool):
        """Updates the UI with progress from the alignment worker."""
        self.status_label.setText(f"Status: {message}")
        if power > -900:  # Filter out initial -999 values
            self.power_label.setText(f"{power:.2f} dBm")
            self.power_bar.setValue(int(power))
        logger.info(message)

    @Slot(str, float, float, object, object)
    def on_alignment_finished(
        self, status: str, initial_power: float, final_power: float, initial_positions: dict, final_positions: dict
    ):
        self.reset_buttons()  # This call is now valid
        self.status_label.setText("Status: Idle")

        if status == "Alignment successful" and initial_positions and final_positions:
            self.power_label.setText(f"{final_power:.2f} dBm")
            self.power_bar.setValue(int(final_power))

            gain = final_power - initial_power

            # --- CALCULATE DELTAS ---
            delta_lx = final_positions["left_x"] - initial_positions["left_x"]
            delta_ly = final_positions["left_y"] - initial_positions["left_y"]
            delta_rx = final_positions["right_x"] - initial_positions["right_x"]
            delta_ry = final_positions["right_y"] - initial_positions["right_y"]

            summary_text = f"""<b>Alignment Complete!</b><br><br>
            Status: {status}<br>
            Initial Power: {initial_power:.2f} dBm<br>
            Final Power: <b>{final_power:.2f} dBm</b><br>
            Total Gain: <font color='{"green" if gain >= 0 else "red"}'>{gain:+.2f} dB</font><br><br>
            <u>Final Voltages (V):</u><br>
            Left Stage:  X={final_positions["left_x"]:.2f} <font color='#666'>(Δ{delta_lx:+.2f})</font>, Y={final_positions["left_y"]:.2f} <font color='#666'>(Δ{delta_ly:+.2f})</font><br>
            Right Stage: X={final_positions["right_x"]:.2f} <font color='#666'>(Δ{delta_rx:+.2f})</font>, Y={final_positions["right_y"]:.2f} <font color='#666'>(Δ{delta_ry:+.2f})</font>
            """
            QMessageBox.information(self, "Alignment Summary", summary_text)
        else:
            QMessageBox.warning(self, "Alignment Stopped", f"Alignment finished with status: {status}")

    @Slot(int, int)
    def on_mapping_progress(self, percent: int, total_points: int):
        """Updates the mapping progress bar and status label."""
        self.status_label.setText(f"Status: Mapping... ({percent}%)")
        self.map_progress.setValue(percent)
        self.map_progress.setFormat(f"{percent}% ({total_points} points)")

    @Slot(object, object, object)
    def on_mapping_finished(self, x_coords, y_coords, z_power_grid):
        """Receives the completed map data, updates the plot, and resets the UI."""
        logger.info("Mapping finished. Updating 3D plot.")
        self.plot3d_widget.update_plot(x_coords, y_coords, z_power_grid)

        # --- MAPPING SUMMARY ---
        peak_power = z_power_grid.max()
        peak_indices = np.unravel_index(np.argmax(z_power_grid), z_power_grid.shape)
        peak_x = x_coords[peak_indices[0]]
        peak_y = y_coords[peak_indices[1]]

        self.plot3d_widget.title_label.setText(
            f"Power Map (Peak: {peak_power:.3f} mW at X={peak_x:.2f}, Y={peak_y:.2f} µm)"
        )

        self.reset_buttons()
        self.status_label.setText("Status: Idle")
        self.map_progress.setVisible(False)
        QMessageBox.information(self, "Map Complete", "Power map has been generated.")

    @Slot(str)
    def on_worker_error(self, message: str):
        """Handles errors reported by the worker thread."""
        QMessageBox.critical(self, "Worker Error", message)
        self.reset_buttons()  # This call is now valid
        self.status_label.setText("Status: Error!")
        self.map_progress.setVisible(False)

    def cleanup(self):
        """Gracefully shuts down the worker thread."""
        # --- MODIFICATION: Check if worker/thread exist before using ---
        if self.worker_thread and self.worker_thread.isRunning():
            if self.alignment_worker:
                self.alignment_worker.stop()
            self.worker_thread.quit()
            if not self.worker_thread.wait(2000):
                logger.warning("Alignment worker thread did not exit gracefully.")
                self.worker_thread.terminate()
