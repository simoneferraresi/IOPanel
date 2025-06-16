import logging

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hardware.alignment_worker import AlignmentSettings, AlignmentWorker, MappingSettings
from hardware.interfaces import AbstractCT400
from hardware.piezo import PiezoController
from ui.plot_widgets import Plot3DWidget

logger = logging.getLogger("LabApp.AlignmentPanel")


class AlignmentPanel(QWidget):
    # --- MODIFICATION: Define the signal to carry a generic 'object' ---
    # This is the most robust way to pass custom Python objects in PySide6.
    start_alignment_requested = Signal(object)
    start_mapping_requested = Signal(object)

    def __init__(
        self,
        ct400: AbstractCT400,
        piezo_left: PiezoController,
        piezo_right: PiezoController,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.ct400 = ct400
        self.piezo_left = piezo_left
        self.piezo_right = piezo_right

        self._init_ui()
        self._init_worker()

    def _init_ui(self):
        """Initializes all UI elements for the alignment and mapping panel."""
        main_layout = QHBoxLayout(self)

        # --- Left Side: All Controls ---
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_widget.setMaximumWidth(350)

        # -- Group 1: Auto Alignment --
        align_group = QGroupBox("Auto Alignment")
        align_form = QFormLayout()
        align_form.setSpacing(10)

        self.butt_coupling_cb = QCheckBox("Butt Coupling")
        self.top_coupling_cb = QCheckBox("Top Coupling")
        self.butt_coupling_cb.setChecked(True)
        self.butt_coupling_cb.toggled.connect(lambda checked: self.top_coupling_cb.setChecked(not checked))
        self.top_coupling_cb.toggled.connect(lambda checked: self.butt_coupling_cb.setChecked(not checked))
        coupling_layout = QHBoxLayout()
        coupling_layout.addWidget(self.butt_coupling_cb)
        coupling_layout.addWidget(self.top_coupling_cb)
        align_form.addRow("Coupling Type:", coupling_layout)

        self.iterations_spin = QSpinBox(minimum=1, maximum=20, value=2)
        align_form.addRow("Iterations:", self.iterations_spin)

        self.step_spin = QDoubleSpinBox(minimum=10, maximum=1000, value=100, suffix=" nm")
        align_form.addRow("Alignment Step:", self.step_spin)

        self.avg_spin = QSpinBox(minimum=1, maximum=10, value=1)
        align_form.addRow("Avg. Samples/Point:", self.avg_spin)

        self.align_button = QPushButton("Start AutoAlignment")
        self.align_button.setCheckable(True)
        self.align_button.setMinimumHeight(30)
        align_form.addRow(self.align_button)

        self.power_label = QLabel("Measured Power: -- dBm")
        font = self.power_label.font()
        font.setPointSize(12)
        self.power_label.setFont(font)
        align_form.addRow(self.power_label)

        align_group.setLayout(align_form)
        controls_layout.addWidget(align_group)

        # -- Group 2: Power Mapping --
        map_group = QGroupBox("Power Mapping (for 3D Plot)")
        map_layout = QFormLayout()
        map_layout.setSpacing(10)

        # --- FIX IS HERE AND IN THE FOLLOWING SPINBOXES ---
        # X-Axis Mapping Parameters
        self.x_min_spin = QSpinBox(minimum=-5000, maximum=5000, value=-3000, suffix=" nm")
        self.x_min_spin.setSingleStep(100)
        self.x_max_spin = QSpinBox(minimum=-5000, maximum=5000, value=3000, suffix=" nm")
        self.x_max_spin.setSingleStep(100)
        map_layout.addRow("X Range (min/max):", self._create_hbox(self.x_min_spin, self.x_max_spin))

        self.x_step_spin = QSpinBox(minimum=10, maximum=1000, value=500, suffix=" nm")
        self.x_step_spin.setSingleStep(10)
        map_layout.addRow("X Step:", self.x_step_spin)

        # Y-Axis Mapping Parameters
        self.y_min_spin = QSpinBox(minimum=-5000, maximum=5000, value=-3000, suffix=" nm")
        self.y_min_spin.setSingleStep(100)
        self.y_max_spin = QSpinBox(minimum=-5000, maximum=5000, value=3000, suffix=" nm")
        self.y_max_spin.setSingleStep(100)
        map_layout.addRow("Y Range (min/max):", self._create_hbox(self.y_min_spin, self.y_max_spin))

        self.y_step_spin = QSpinBox(minimum=10, maximum=1000, value=500, suffix=" nm")
        self.y_step_spin.setSingleStep(10)
        map_layout.addRow("Y Step:", self.y_step_spin)

        # Target Stage Selection
        self.map_left_radio = QRadioButton("Map Left Stage")
        self.map_right_radio = QRadioButton("Map Right Stage")
        self.map_left_radio.setChecked(True)
        map_layout.addRow("Target Stage:", self._create_hbox(self.map_left_radio, self.map_right_radio))

        # Mapping Action Button and Progress Bar
        self.map_button = QPushButton("Do Map")
        self.map_button.setCheckable(True)
        self.map_button.setMinimumHeight(30)
        self.map_progress = QProgressBar()
        self.map_progress.setVisible(False)
        map_layout.addRow(self.map_button)
        map_layout.addRow(self.map_progress)

        map_group.setLayout(map_layout)
        controls_layout.addWidget(map_group)

        controls_layout.addStretch()

        # --- Right Side: 3D Plot ---
        self.plot3d_widget = Plot3DWidget()

        # --- Final Assembly ---
        main_layout.addWidget(controls_widget)
        main_layout.addWidget(self.plot3d_widget, stretch=1)

        # --- Connect signals to slots ---
        self.align_button.clicked.connect(self.toggle_alignment)
        self.map_button.clicked.connect(self.toggle_mapping)

    def _create_hbox(self, *widgets):
        """Helper to create a QHBoxLayout for a row."""
        layout = QHBoxLayout()
        for widget in widgets:
            layout.addWidget(widget)
        return layout

    def _init_worker(self):
        # This method remains unchanged.
        self.worker_thread = QThread(self)
        self.alignment_worker = AlignmentWorker(self.ct400, self.piezo_left, self.piezo_right)
        self.alignment_worker.moveToThread(self.worker_thread)
        self.start_alignment_requested.connect(self.alignment_worker.run_alignment)
        self.alignment_worker.progress_updated.connect(self.on_progress_update)
        self.alignment_worker.alignment_finished.connect(self.on_alignment_finished)
        self.alignment_worker.error_occurred.connect(self.on_worker_error)
        self.start_mapping_requested.connect(self.alignment_worker.run_mapping)
        self.alignment_worker.mapping_progress.connect(self.on_mapping_progress)
        self.alignment_worker.mapping_finished.connect(self.on_mapping_finished)
        self.worker_thread.start()

    @Slot(bool)
    def toggle_alignment(self, checked):
        # This method remains unchanged.
        if checked:
            settings = AlignmentSettings(
                iterations=self.iterations_spin.value(),
                step_nm=self.step_spin.value(),
                samples_per_point=self.avg_spin.value(),
                coupling_type="butt" if self.butt_coupling_cb.isChecked() else "top",
            )
            self.align_button.setText("Stop Alignment")
            self.start_alignment_requested.emit(settings)
        else:
            self.alignment_worker.stop()
            self.align_button.setText("Start AutoAlignment")

    @Slot(bool)
    def toggle_mapping(self, checked):
        """Starts or stops the power mapping process."""
        if checked:
            self.map_button.setText("Stop Map")
            self.map_progress.setValue(0)
            self.map_progress.setVisible(True)

            settings = MappingSettings(
                x_min_nm=self.x_min_spin.value(),
                x_max_nm=self.x_max_spin.value(),
                x_step_nm=self.x_step_spin.value(),
                y_min_nm=self.y_min_spin.value(),
                y_max_nm=self.y_max_spin.value(),
                y_step_nm=self.y_step_spin.value(),
                samples_per_point=self.avg_spin.value(),  # Reuse from alignment
                stage_to_map="left" if self.map_left_radio.isChecked() else "right",
            )
            self.start_mapping_requested.emit(settings)
        else:
            self.alignment_worker.stop()
            self.map_button.setText("Do Map")
            self.map_progress.setVisible(False)

    @Slot(str, float)
    def on_progress_update(self, message: str, power: float):
        """Updates the UI with progress from the alignment worker."""
        self.power_label.setText(f"Measured Power: {power:.2f} dBm")
        logger.info(message)

    @Slot(str, float)
    def on_alignment_finished(self, status: str, final_power: float):
        """Handles the completion of the alignment process."""
        self.power_label.setText(f"Final Power: {final_power:.2f} dBm")
        QMessageBox.information(self, "Alignment Complete", f"Alignment finished with status: {status}")
        self.align_button.setChecked(False)
        self.align_button.setText("Start AutoAlignment")

    @Slot(int, int)
    def on_mapping_progress(self, percent: int, total_points: int):
        """Updates the mapping progress bar."""
        self.map_progress.setValue(percent)
        self.map_progress.setFormat(f"{percent}% ({total_points} points)")

    @Slot(object, object, object)
    def on_mapping_finished(self, x_coords, y_coords, z_power_grid):
        """Receives the completed map data and sends it to the 3D plot."""
        logger.info("Mapping finished. Updating 3D plot.")
        self.plot3d_widget.update_plot(x_coords, y_coords, z_power_grid)
        self.map_button.setChecked(False)
        self.map_button.setText("Do Map")
        self.map_progress.setVisible(False)
        QMessageBox.information(self, "Map Complete", "Power map has been generated.")

    @Slot(str)
    def on_worker_error(self, message: str):
        """Handles errors reported by the worker thread."""
        QMessageBox.critical(self, "Worker Error", message)
        # Reset both buttons in case of an error
        self.align_button.setChecked(False)
        self.align_button.setText("Start AutoAlignment")
        self.map_button.setChecked(False)
        self.map_button.setText("Do Map")
        self.map_progress.setVisible(False)

    def cleanup(self):
        """Gracefully shuts down the worker thread."""
        if self.worker_thread.isRunning():
            self.alignment_worker.stop()
            self.worker_thread.quit()
            self.worker_thread.wait(2000)
