"""Dry-run preview for the Issue #140 CT400 calibration matrix."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config_model import AppConfig
from logic.ct400_calibration import ISSUE_140_CASES, build_calibration_matrix


class CT400CalibrationDialog(QDialog):
    """Preview the approved matrix without claiming or operating the instrument."""

    def __init__(self, config: AppConfig, parent=None, enabled_detectors: tuple[str, ...] = ()):
        super().__init__(parent)
        self.config = config
        self.enabled_detectors = enabled_detectors
        self.setWindowTitle("CT400 ETA Calibration — Dry Run")
        self.resize(1000, 680)
        layout = QVBoxLayout(self)
        self.gate_notice = QLabel(
            "Dry-run planning only. Physical campaign execution is disabled while Issue #125 connection-state "
            "safety remains open. No instrument calls are made from this dialog."
        )
        self.gate_notice.setWordWrap(True)
        layout.addWidget(self.gate_notice)

        self.instrument_summary = QLabel(self._settings_summary())
        self.instrument_summary.setWordWrap(True)
        layout.addWidget(self.instrument_summary)

        self.confirm_extra_detectors = QCheckBox(
            "I explicitly confirm DE2–DE4 are connected and validated for this setup (case F)."
        )
        layout.addWidget(self.confirm_extra_detectors)

        form = QFormLayout()
        self.export_directory = QLineEdit(str(Path.home() / "Documents" / "IOPanel" / "CT400-ETA-calibration"))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_directory)
        export_row = QHBoxLayout()
        export_row.addWidget(self.export_directory)
        export_row.addWidget(browse)
        form.addRow("Export directory", export_row)
        layout.addLayout(form)

        self.matrix = QTableWidget(0, 8)
        self.matrix.setHorizontalHeaderLabels(
            ["Case", "Repetition", "Connect speed", "Range (nm)", "Resolution", "Detectors", "Samples", "Nominal"]
        )
        self.matrix.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.matrix, stretch=1)

        self.block_notice = QLabel(
            "Speed blocks: complete all 10 nm/s runs first. Pause for ordinary operator disconnect/reconnect at "
            "5 nm/s, then require fresh confirmation. The editable scan speed does not change connected laser speed."
        )
        self.block_notice.setWordWrap(True)
        layout.addWidget(self.block_notice)
        self.validation = QLabel()
        layout.addWidget(self.validation)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start campaign")
        self.start_button.setEnabled(False)
        self.start_button.setToolTip("Physical execution is not implemented; Issue #125 remains open.")
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(self.start_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.refresh_preview()

    def _settings_summary(self) -> str:
        cfg = self.config
        return (
            f"Selected backend: {cfg.instruments.ct400_backend}. Configured speed for the next ordinary Connect: "
            f"{cfg.scan_defaults.speed_nm_s} nm/s (configuration value, not current instrument readback). "
            f"Configured wavelength limits: {cfg.scan_defaults.min_wavelength_nm:g}–"
            f"{cfg.scan_defaults.max_wavelength_nm:g} nm (not physically verified laser limits). "
            f"Configured laser input: {cfg.scan_defaults.input_port}; configured power: "
            f"{cfg.scan_defaults.laser_power:g} {cfg.scan_defaults.power_unit}. "
            f"Enabled in scan panel now: {', '.join(self.enabled_detectors) or 'none'}; case F requests DE1–DE4."
        )

    def _choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Calibration export directory", self.export_directory.text())
        if selected:
            self.export_directory.setText(selected)

    def refresh_preview(self) -> None:
        try:
            runs = build_calibration_matrix()
            lower = self.config.scan_defaults.min_wavelength_nm
            upper = self.config.scan_defaults.max_wavelength_nm
            invalid_cases = [
                case.case_id
                for case in ISSUE_140_CASES
                if case.start_wavelength_nm < lower or case.end_wavelength_nm > upper
            ]
            self.matrix.setRowCount(len(runs))
            for row, run in enumerate(runs):
                values = (
                    run.case_id,
                    str(run.repetition),
                    f"{run.speed_block_nm_s:g} nm/s",
                    f"{run.start_wavelength_nm:g}–{run.end_wavelength_nm:g}",
                    f"{run.resolution_pm} pm",
                    ", ".join(run.detectors),
                    f"{run.predicted_sample_count:,}",
                    f"{run.nominal_sweep_seconds:.1f} s",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.matrix.setItem(row, column, item)
            self.matrix.resizeColumnsToContents()
            if invalid_cases:
                self.validation.setText(
                    f"Configured wavelength limits do not include cases: {', '.join(invalid_cases)}. "
                    "This is not connected-laser validation."
                )
            else:
                self.validation.setText(
                    f"{len(runs)} planned measurements. Configuration-range check passed; connected laser range "
                    "and detector wiring remain unverified. Case F requires explicit detector confirmation."
                )
        except ValueError as error:
            self.matrix.setRowCount(0)
            self.validation.setText(f"Invalid plan: {error}")
