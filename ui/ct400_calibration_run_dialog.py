"""Operator-supervised CT400 ETA campaign using the existing scan UI/worker.

No vendor DLL or hardware methods are called by this dialog. Real acquisitions
are started ONLY through CT400ControlPanel._start_scan after explicit preflight,
and stopped ONLY through its existing _stop_scan cancellation workflow.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from hardware.ct400 import CT400
from hardware.ct400_types import Detector, LaserInput
from logic.ct400_calibration import CalibrationCampaign, CalibrationRun, append_calibration_record, build_calibration_matrix

logger = logging.getLogger("LabApp.ct400_calibration_campaign")


class CT400CalibrationRunDialog(QDialog):
    """GUI-thread coordinator; retains ownership until the scan thread exits."""

    _INTER_RUN_DELAY_MS = 1500

    def __init__(self, main_window):
        super().__init__(main_window)
        self.window = main_window
        self.panel = main_window.control_panel
        self.setWindowTitle("CT400 ETA Calibration — Supervised Campaign")
        self.resize(680, 450)
        self.runs = build_calibration_matrix()
        self.campaign: CalibrationCampaign | None = None
        self.directory: Path | None = None
        self.index = 0
        self.active_run: CalibrationRun | None = None
        self._active_device = None
        self._active_started: float | None = None
        self._measurement = None
        self._timing: dict[str, Any] | None = None
        self._halt_requested = False
        self._pause_requested = False
        self._approved_speed: float | None = None
        self._laser_input: LaserInput | None = None
        self._power_mw: float | None = None
        self._original: dict[str, Any] = {}
        self._state = "idle"
        self.next_timer = QTimer(self)
        self.next_timer.setSingleShot(True)
        self.next_timer.timeout.connect(self._dispatch_next)
        self.panel.scan_timing_ready.connect(self._on_scan_timing)
        self.panel.scan_data_ready.connect(self._on_scan_data)
        self.panel.operation_finished.connect(self._on_scan_finished)

        layout = QVBoxLayout(self)
        self.status = QLabel("Inactive — no scans have been scheduled.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QLabel("0 / 18 planned runs")
        layout.addWidget(self.progress)

        form = QFormLayout()
        self.path = QLineEdit(str(Path.home() / "Documents" / "IOPanel" / "CT400-ETA-calibration"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_directory)
        location = QHBoxLayout()
        location.addWidget(self.path)
        location.addWidget(browse)
        form.addRow("Records folder", location)
        self.operator = QLineEdit()
        self.operator.setPlaceholderText("Operator or session identifier")
        form.addRow("Operator/session", self.operator)
        self.physical_min = QDoubleSpinBox()
        self.physical_max = QDoubleSpinBox()
        for spin in (self.physical_min, self.physical_max):
            spin.setRange(1000, 2000)
            spin.setDecimals(2)
            spin.setValue(1550)
        form.addRow("Independently verified laser minimum (nm)", self.physical_min)
        form.addRow("Independently verified laser maximum (nm)", self.physical_max)
        layout.addLayout(form)

        self.confirm_range = QCheckBox("I independently verified the connected laser supports every planned wavelength.")
        self.confirm_power = QCheckBox("I verified the connected input, detector wiring and 1 mW or selected approved laser power.")
        self.confirm_risk = QCheckBox(
            "I understand native ScanStart can stall and the operator must supervise and follow the lab safe-state procedure."
        )
        self.confirm_four = QCheckBox("DE2–DE4 are wired and approved (otherwise the three F runs will be skipped).")
        for check in (self.confirm_range, self.confirm_power, self.confirm_risk, self.confirm_four):
            layout.addWidget(check)
        self.notice = QLabel(
            "The physical laser speed is set at Connect (not read back). Initial 10 nm/s runs require an "
            "already connected and confirmed 10 nm/s input. After 15 planned runs, the campaign pauses "
            "for manual Disconnect, explicit speed setup and normal Connect at 5 nm/s. "
            "An unexpected scan error halts the campaign; no auto-resume."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        row = QHBoxLayout()
        self.start_button = QPushButton("Start supervised campaign")
        self.start_button.clicked.connect(self.start_campaign)
        self.pause_button = QPushButton("Pause after current")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button = QPushButton("Stop campaign")
        self.stop_button.clicked.connect(self.stop_campaign)
        row.addWidget(self.start_button)
        row.addWidget(self.pause_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)

        speed_row = QHBoxLayout()
        self.prepare_speed = QPushButton("Prepare 5 nm/s next Connect")
        self.prepare_speed.clicked.connect(self.prepare_next_speed)
        self.confirm_speed = QPushButton("Confirm reconnected 5 nm/s")
        self.confirm_speed.clicked.connect(self.confirm_speed_checkpoint)
        speed_row.addWidget(self.prepare_speed)
        speed_row.addWidget(self.confirm_speed)
        layout.addLayout(speed_row)

        self._refresh_controls()

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self.status.setText(message)
        self.progress.setText(f"{self.index} / {len(self.runs)} planned runs processed")
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        self.start_button.setEnabled(self._state == "idle")
        self.pause_button.setEnabled(self._state in ("ready", "scanning", "paused"))
        self.pause_button.setText("Resume" if self._state == "paused" else "Pause after current")
        self.stop_button.setEnabled(self._state in ("ready", "scanning", "paused", "checkpoint", "stopping"))
        self.prepare_speed.setEnabled(self._state == "checkpoint")
        self.confirm_speed.setEnabled(self._state == "checkpoint")
        editable = self._state == "idle"
        for widget in (
            self.path, self.operator, self.physical_min, self.physical_max, self.confirm_range,
            self.confirm_power, self.confirm_risk, self.confirm_four,
        ):
            widget.setEnabled(editable or (widget is self.confirm_range and self._state == "checkpoint"))

    def _choose_directory(self) -> None:
        if self._state != "idle":
            return
        chosen = QFileDialog.getExistingDirectory(self, "Calibration records", self.path.text())
        if chosen:
            self.path.setText(chosen)

    def _physical_ready(self, speed: float) -> bool:
        w = self.window
        return bool(
            getattr(w, "_ct400_operation_state", None) is not None
            and w._ct400_operation_state.name == "IDLE"
            and isinstance(w.ct400_device, CT400)
            and w.ct400_device is self.panel.ct400
            and w._ct400_connection_configured
            and not w._ct400_scan_state_uncertain
            and not w._ct400_recovery_disconnect_completed
            and w._ct400_cleanup_laser_input is self._laser_input
            and w._ct400_connected_laser_input is self._laser_input
            and self.panel.connected_laser_input is self._laser_input
            and w.is_ct400_connected_state
            and w._ct400_connect_speed_nm_s == speed
            and w.config.scan_defaults.speed_nm_s == speed
            and self.panel.scan_thread is None
            and not self.panel.scanning
            and not w.histogram_control.is_busy()
            and self.panel.ct400_operation_state == "IDLE"
        )

    def start_campaign(self) -> None:
        if self._state != "idle":
            return
        try:
            if not self.operator.text().strip():
                raise ValueError("Enter a session/operator identifier.")
            if not all(check.isChecked() for check in (self.confirm_range, self.confirm_power, self.confirm_risk)):
                raise ValueError("Complete the three operator preflight confirmations.")
            if self.physical_min.value() > min(run.start_wavelength_nm for run in self.runs):
                raise ValueError("Verified laser minimum does not include all planned runs.")
            if self.physical_max.value() < max(run.end_wavelength_nm for run in self.runs):
                raise ValueError("Verified laser maximum does not include all planned runs.")
            self._laser_input = self.window._ct400_connected_laser_input
            if not isinstance(self._laser_input, LaserInput) or not self._physical_ready(10):
                raise ValueError("Use ordinary Connect on the approved input at 10 nm/s before campaign Start.")
            self._power_mw = self.panel._get_laser_power_mw()
            if not math.isfinite(self._power_mw) or self._power_mw <= 0:
                raise ValueError("Invalid approved scan laser power.")
            self.directory = Path(self.path.text().strip()).expanduser()
            if not self.directory.is_absolute():
                raise ValueError("Use an absolute records directory.")
            campaign_id = self.runs[0].campaign_id
            if any((self.directory / f"{campaign_id}.{ext}").exists() for ext in ("jsonl", "json", "csv")):
                raise FileExistsError("Campaign record files already exist; refusing to overwrite.")
            answer = QMessageBox.question(
                self, "Confirm supervised physical calibration",
                f"Run up to 18 supervised scans on Input {self._laser_input.value} at "
                f"{self._power_mw:g} mW? DE2–DE4 "
                f"{'approved' if self.confirm_four.isChecked() else 'NOT approved (F skipped)'}. "
                "The native ScanStart stall risk remains unresolved. "
                "Do not leave the instrument unattended. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._original = self._capture_scan_settings()
            self.panel.reserve_calibration_campaign(campaign_id)
            self.campaign = CalibrationCampaign(
                campaign_id, datetime.now(UTC).isoformat(),
                settings={
                    "physical": True, "laser_input": self._laser_input.value,
                    "power_mw": self._power_mw,
                    "verified_laser_range_nm": [self.physical_min.value(), self.physical_max.value()],
                    "planned_run_count": len(self.runs),
                    "four_detector_wiring_confirmed": self.confirm_four.isChecked(),
                },
                provenance={"operator_session": self.operator.text().strip()},
            )
            self._active_device = self.window.ct400_device
            self._approved_speed = 10.0
            self._set_state("ready", "Supervised campaign started; 10 nm/s block is authorized.")
            self.next_timer.start(self._INTER_RUN_DELAY_MS)
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Calibration preflight blocked", str(exc))

    def _capture_scan_settings(self) -> dict[str, Any]:
        p = self.panel
        return {
            "start": p.initial_wl.text(), "end": p.final_wl.text(),
            "resolution": p.resolution.text(), "speed": p.motor_speed.text(),
            "power": p.laser_power.text(), "unit": p.power_unit.currentText(),
            "input": p.input_port.currentIndex(),
            "detectors": {detector: box.isChecked() for detector, box in p.scan_detector_cbs.items()},
            "config_connect_speed": self.window.config.scan_defaults.speed_nm_s,
        }

    def _restore_scan_settings(self) -> None:
        if not self._original:
            return
        p = self.panel
        original = self._original
        p.initial_wl.setText(original["start"])
        p.final_wl.setText(original["end"])
        p.resolution.setText(original["resolution"])
        # Keep the panel speed aligned to the approved/current Connect-time\n        # setting; restoring an obsolete 10 nm/s label after a 5 nm/s\n        # reconnect would falsely describe the actual laser configuration.\n        connected_speed = self.window._ct400_connect_speed_nm_s\n        p.motor_speed.setText(str(connected_speed if connected_speed is not None else self.window.config.scan_defaults.speed_nm_s))
        p.laser_power.setText(original["power"])
        p.power_unit.setCurrentText(original["unit"])
        p.input_port.setCurrentIndex(original["input"])
        for detector, checked in original["detectors"].items():
            p.scan_detector_cbs[detector].setChecked(checked)
        # Do not silently revert Connect-time speed while still connected at a
        # different speed: the config reflects the operator's last checkpoint.
        self._original = {}

    def _dispatch_next(self) -> None:
        if self._state != "ready" or self.active_run is not None or self.campaign is None:
            return
        if self.index == len(self.runs):
            self._terminate("completed", "All planned runs have a durable terminal record.")
            return
        run = self.runs[self.index]
        if run.speed_block_nm_s != self._approved_speed:
            self._set_state("checkpoint", "Speed block change: Disconnect normally, prepare 5 nm/s, then Connect normally and confirm.")
            return
        if not self._physical_ready(run.speed_block_nm_s) or self.window.ct400_device is not self._active_device:
            self._terminate("failed", "Physical CT400 ownership, connected speed or safety gate changed. Campaign halted.")
            return
        if len(run.detectors) > 1 and not self.confirm_four.isChecked():
            if not self._persist(run, {"outcome": "skipped", "reason": "DE2–DE4 wiring not confirmed"}):
                return
            self.index += 1
            self._set_state("ready", f"Skipped case {run.case_id} repetition {run.repetition}: detector wiring not confirmed.")
            self.next_timer.start(self._INTER_RUN_DELAY_MS)
            return
        p = self.panel
        try:
            p.initial_wl.setText(str(run.start_wavelength_nm))
            p.final_wl.setText(str(run.end_wavelength_nm))
            p.resolution.setText(str(run.resolution_pm))
            p.motor_speed.setText(str(run.speed_block_nm_s))
            p.power_unit.setCurrentText("mW")
            p.laser_power.setText(str(self._power_mw))
            p.input_port.setCurrentIndex(p.input_port.findData(self._laser_input))
            for detector, box in p.scan_detector_cbs.items():
                if detector is not Detector.DE_1:
                    box.setChecked(detector.name in run.detectors)
            p.set_next_scan_id(run.run_id)
            self.active_run = run
            self._active_started = time.perf_counter()
            self._measurement = None
            self._timing = None
            self._set_state("scanning", f"Case {run.case_id} / repetition {run.repetition}: awaiting CT400 worker completion.")
            p._start_scan()
            if not p.scanning or p.scan_worker is None or p.scan_worker.scan_id != run.run_id:
                self._terminate("failed", "Normal scan panel rejected launch; campaign halted.")
        except Exception as exc:  # noqa: BLE001  # Prevent the campaign from scheduling another scan.
            logger.exception("Failed to launch supervised calibration scan")
            self._terminate("failed", f"Could not dispatch next scan through the normal panel: {exc}")

    def _on_scan_timing(self, payload: object) -> None:
        if self.active_run is None or not isinstance(payload, dict):
            return
        if payload.get("scan_id") == self.active_run.run_id:
            self._timing = dict(payload)

    def _on_scan_data(self, measurement: object) -> None:
        if self.active_run is not None and getattr(measurement, "scan_id", None) == self.active_run.run_id:
            self._measurement = measurement

    def _on_scan_finished(self) -> None:
        run = self.active_run
        if run is None or self.campaign is None:
            return
        # The ordinary panel emits operation_finished only after ScanWorker and
        # its thread are cleaned up, including final selected-input disable.
        timing = self._timing
        outcome = str(timing.get("outcome", "failed")) if timing else "failed"
        reason = ""
        if timing is None:
            reason = "Scan worker finished without a matching timing record"
        elif timing.get("cleanup_failed") or timing.get("failed_stages"):
            reason = "Scan worker reported failed stage(s) or cleanup"
            outcome = "failed"
        elif timing.get("simulated") is not False:
            reason = "Hardware calibration received a simulated or unknown backend result"
            outcome = "failed"
        elif timing.get("scan_panel_speed_differs_from_configured"):
            reason = "Requested and Connect-time speed differed"
            outcome = "failed"
        elif self.window._ct400_scan_state_uncertain:
            reason = "CT400 native state requires explicit operator recovery"
            outcome = "failed"
        elif outcome == "success" and self._measurement is None:
            reason = "No matching scan measurement data"
            outcome = "failed"
        elif outcome not in ("success", "warning", "cancellation"):
            outcome = "failed"
            reason = "Unexpected scan result"
        if self.window.ct400_device is not self._active_device:
            outcome = "failed"
            reason = "CT400 device was replaced during the campaign"
        recorded = {
            "outcome": outcome,
            "reason": reason,
            "stage_seconds": timing.get("stage_seconds", {}) if timing else {},
            "worker_duration_seconds": timing.get("worker_duration_seconds") if timing else None,
            "gui_start_to_completion_seconds": (
                max(0.0, time.perf_counter() - self._active_started) if self._active_started is not None else None
            ),
            "scan_worker_gui_duration_seconds": timing.get("gui_start_to_completion_seconds") if timing else None,
            "gui_completion_dispatch_latency_seconds": (
                timing.get("gui_completion_dispatch_latency_seconds") if timing else None
            ),
            "returned_sample_count": timing.get("returned_sample_count") if timing else None,
            "configured_speed_nm_s_at_connect": timing.get("configured_speed_nm_s_at_connect") if timing else None,
            "scan_panel_speed_differs_from_configured": (
                timing.get("scan_panel_speed_differs_from_configured") if timing else None
            ),
            "failed_stages": timing.get("failed_stages") if timing else ["missing_timing"],
            "cleanup_failed": timing.get("cleanup_failed") if timing else None,
        }
        self.active_run = None
        self._active_started = None
        self._timing = None
        self._measurement = None
        if not self._persist(run, recorded):
            return
        self.index += 1
        if self._halt_requested or outcome == "cancellation":
            self._terminate("stopped", "Campaign stopped; no further scans will be scheduled.")
        elif outcome == "failed":
            self._terminate("failed", f"Campaign halted after failed run {run.case_id}: {reason}.")
        elif self._pause_requested or outcome == "warning":
            self._pause_requested = False
            self._set_state("paused", "Campaign paused for operator review; no further scans will start.")
        else:
            self._set_state("ready", f"Run {self.index} recorded. Waiting before next scan.")
            self.next_timer.start(self._INTER_RUN_DELAY_MS)

    def _persist(self, run: CalibrationRun, details: dict[str, Any]) -> bool:
        if self.directory is None or self.campaign is None:
            raise RuntimeError("Campaign has no active record destination")
        record = {
            **asdict(run), "campaign_id": self.campaign.campaign_id,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "laser_input": self._laser_input.value if self._laser_input else None,
            "power_mw": self._power_mw,
            "simulated": False,
            **details,
        }
        try:
            append_calibration_record(self.directory, self.campaign, record)
            return True
        except Exception as exc:  # noqa: BLE001  # Journal write may succeed before manifest/CSV fails.
            logger.exception("Calibration journal failure; no next scan is permitted")
            self._terminate("failed", f"Failed to persist run; inspect JSONL before any retry: {exc}")
            return False

    def toggle_pause(self) -> None:
        if self._state == "scanning":
            self._pause_requested = True
            self._set_state("scanning", "Pause requested; waiting for worker cleanup before stopping.")
        elif self._state == "ready":
            self.next_timer.stop()
            self._set_state("paused", "Paused between runs; no acquisition is active.")
        elif self._state == "paused":
            self._set_state("ready", "Resuming planned scans after operator confirmation.")
            self.next_timer.start(self._INTER_RUN_DELAY_MS)

    def stop_campaign(self) -> None:
        if self._state in ("completed", "stopped", "failed", "idle"):
            return
        self.next_timer.stop()
        self._halt_requested = True
        if self.active_run is not None:
            self._set_state("stopping", "Stopping current scan through the normal CT400 ScanWorker.")
            self.panel._stop_scan(cancelled=True)
        else:
            self._terminate("stopped", "Campaign stopped between runs.")

    def prepare_next_speed(self) -> None:
        if self._state != "checkpoint" or self.active_run is not None:
            return
        w = self.window
        if (
            w._ct400_operation_state.name != "IDLE" or w._ct400_connected_laser_input is not None
            or w._ct400_device_has_unresolved_laser_state()
            or w._ct400_scan_state_uncertain
        ):
            QMessageBox.warning(self, "Disconnect first", "Use the ordinary CT400 Disconnect action and independently verify the lab safe state.")
            return
        answer = QMessageBox.question(
            self, "Prepare next Connect speed",
            "After independent safe-state confirmation, change the in-memory speed for the NEXT ordinary "
            "Connect to 5 nm/s? No CT400 command is issued now. You must then connect manually.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        w.config.scan_defaults.speed_nm_s = 5
        self.panel.motor_speed.setText("5")
        self._set_state("checkpoint", "Next Connect is configured for 5 nm/s. Use Instruments → Connect, then confirm here.")

    def confirm_speed_checkpoint(self) -> None:
        if self._state != "checkpoint" or self.active_run is not None:
            return
        if not self.confirm_range.isChecked() or not self._physical_ready(5):
            QMessageBox.warning(self, "Speed checkpoint blocked", "Confirm safe laser range and reconnect at 5 nm/s using the ordinary CT400 action.")
            return
        answer = QMessageBox.question(
            self, "Confirm 5 nm/s block",
            "The application reports 5 nm/s was supplied at Connect; speed cannot be read back. "
            "Have you independently checked the connected input, laser setup, power and approved speed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._approved_speed = 5.0
            self._set_state("ready", "Operator confirmed 5 nm/s Connect-time speed; resuming remaining runs.")
            self.next_timer.start(self._INTER_RUN_DELAY_MS)

    def _terminate(self, final_state: str, message: str) -> None:
        self.next_timer.stop()
        if self.active_run is not None and self.panel.scanning:
            self._halt_requested = True
            self._set_state("stopping", f"{message} Waiting for active scan cleanup.")
            self.panel._stop_scan(cancelled=True)
            return
        if self.active_run is not None:
            # A missing worker terminal event is not safely recoverable here.
            self._set_state("failed", f"{message} Active scan ownership remains unresolved.")
            return
        if self.campaign is not None:
            try:
                self.panel.release_calibration_campaign(self.campaign.campaign_id)
                self._restore_scan_settings()
            except Exception:  # noqa: BLE001  # Preserve fail-closed ownership on unsuccessful release.
                logger.exception("Cannot release calibration scan ownership")
                self._set_state("failed", "Scan ownership release failed; do not start another scan.")
                return
        self._set_state(final_state, message)

    def closeEvent(self, event) -> None:
        if self._state in ("ready", "scanning", "paused", "checkpoint", "stopping") or self.active_run is not None:
            self.stop_campaign()
            event.ignore()
            self.status.setText("Close deferred until active scan finishes; campaign is stopping.")
            return
        super().closeEvent(event)
