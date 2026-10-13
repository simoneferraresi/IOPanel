"""Attended, opt-in CT400 ETA campaign using the existing serialized ScanWorker.

This dialog never calls the vendor DLL or connects/disconnects a laser. A live
campaign dispatches *only* through CT400ControlPanel.run_calibration_scan,
which retains the normal scan worker, ownership and cleanup lifecycle.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from hardware.ct400 import CT400
from hardware.ct400_types import LaserInput
from logic.ct400_calibration import (
    CalibrationCampaign,
    CalibrationRun,
    append_calibration_record,
    build_calibration_matrix,
)

logger = logging.getLogger("LabApp.ct400_calibration_campaign")


class CT400SupervisedCampaignDialog(QDialog):
    """Single supervised session, no auto-resume across application restarts."""

    _INTER_RUN_DELAY_MS = 2500

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.panel = window.control_panel
        self.runs = build_calibration_matrix()
        self.campaign = CalibrationCampaign(
            self.runs[0].campaign_id,
            datetime.now(UTC).isoformat(),
            {"supervised": True, "planned_run_count": len(self.runs)},
        )
        self.next_index = 0
        self.active_run: CalibrationRun | None = None
        self._timing: dict | None = None
        self._owns_panel = False
        self._device: CT400 | None = None
        self._laser_input: LaserInput | None = None
        self._power_mw: float | None = None
        self._original_fields: dict | None = None
        self._next_timer = QTimer(self)
        self._next_timer.setSingleShot(True)
        self._next_timer.timeout.connect(self._dispatch_next)
        self.state = "ready"
        self._stop_requested = False
        self._close_after_stop = False
        self._shutdown_requested = False
        self._speed_checkpoint_approved: float | None = None

        self.setWindowTitle("CT400 ETA Campaign — Operator Supervised")
        self.setMinimumWidth(700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        layout = QVBoxLayout(self)
        self.notice = QLabel(
            "PHYSICAL CT400 OPERATION — attended use only. Start explicitly arms a real 18-run "
            "campaign via the normal ScanWorker. No automatic Connect/Disconnect, "
            "no automatic speed change, no unattended scheduling. Stop requests "
            "the normal scan cancellation and prevents all later runs."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.progress = QLabel("Not started. 18 planned runs (15 at 10 nm/s; 3 at 5 nm/s).")
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)

        form = QFormLayout()
        self.export_directory = QLineEdit(str(Path.home() / "Documents" / "IOPanel" / "CT400-ETA-calibration"))
        form.addRow("Output directory:", self.export_directory)
        layout.addLayout(form)

        self.confirm_safe = QCheckBox(
            "I am present and have independently verified the approved optical safe state, "
            "range, power, connected laser input, detector wiring and laboratory authorization."
        )
        self.confirm_safe.toggled.connect(self._refresh_controls)
        layout.addWidget(self.confirm_safe)
        self.confirm_extra = QCheckBox("DE2–DE4 are connected and approved for case F; otherwise skip F.")
        layout.addWidget(self.confirm_extra)
        self.auto_next = QCheckBox("Automatically continue within an approved speed block (2.5 s between runs).")
        layout.addWidget(self.auto_next)

        row = QHBoxLayout()
        self.start_button = QPushButton("Start supervised campaign")
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        self.continue_button = QPushButton("Continue next run")
        self.continue_button.clicked.connect(self._continue)
        row.addWidget(self.continue_button)
        self.pause_button = QPushButton("Pause after current run")
        self.pause_button.clicked.connect(self._pause)
        row.addWidget(self.pause_button)
        self.stop_button = QPushButton("Stop campaign")
        self.stop_button.clicked.connect(self._stop)
        row.addWidget(self.stop_button)
        layout.addLayout(row)

        speed_row = QHBoxLayout()
        self.prepare_initial_speed_button = QPushButton("Set next Connect to 10 nm/s")
        self.prepare_initial_speed_button.clicked.connect(self._prepare_initial_connect_speed)
        speed_row.addWidget(self.prepare_initial_speed_button)
        self.prepare_speed_button = QPushButton("Set next Connect to 5 nm/s")
        self.prepare_speed_button.clicked.connect(self._prepare_next_connect_speed)
        speed_row.addWidget(self.prepare_speed_button)
        self.ack_speed_button = QPushButton("Confirm reconnected speed + resume")
        self.ack_speed_button.clicked.connect(self._acknowledge_speed_checkpoint)
        speed_row.addWidget(self.ack_speed_button)
        layout.addLayout(speed_row)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        layout.addWidget(self.close_button)

        self.panel.scan_timing_ready.connect(self._receive_timing)
        self.panel.operation_finished.connect(self._scan_operation_finished)
        self._refresh_controls()

    @property
    def active(self) -> bool:
        return self._owns_panel

    def _physical_ready(self, required_speed: float) -> bool:
        """Fail closed on loss of connection, device identity, speed or input."""
        window = self.window
        device = window.ct400_device
        if not isinstance(device, CT400):
            return False
        if self._device is not None and device is not self._device:
            return False
        if self.panel.ct400 is not device:
            return False
        if (
            not window._ct400_connection_configured
            or window._ct400_scan_state_uncertain
            or window._ct400_recovery_disconnect_completed
            or window._ct400_connected_laser_input is None
            or window._ct400_connected_laser_input is not window._ct400_cleanup_laser_input
            or self.panel.connected_laser_input is not window._ct400_connected_laser_input
            or window._ct400_connected_speed_nm_s != required_speed
            or float(window.config.scan_defaults.speed_nm_s) != required_speed
            or window._ct400_operation_state.name != "IDLE"
            or self.panel.scanning
            or self.panel.scan_thread is not None
        ):
            return False
        selected_input = self.panel.input_port.currentData()
        if not isinstance(selected_input, LaserInput):
            return False
        if self._laser_input is not None:
            return window._ct400_connected_laser_input is self._laser_input
        return window._ct400_connected_laser_input is selected_input

    def _validate_preflight(self) -> None:
        if not self.confirm_safe.isChecked():
            raise ValueError("Operator must confirm physical laboratory readiness")
        if not self._physical_ready(self.runs[self.next_index].speed_block_nm_s):
            raise ValueError("CT400 must be idle and explicitly connected at the required Connect-time speed")
        if self.window.config.scan_defaults.min_wavelength_nm > min(
            r.start_wavelength_nm for r in self.runs
        ) or self.window.config.scan_defaults.max_wavelength_nm < max(r.end_wavelength_nm for r in self.runs):
            raise ValueError("The configured laser wavelength limits do not cover the campaign")
        if self.window._pending_init_close or self.window._pending_ct400_operation_close:
            raise ValueError("Application shutdown is pending")
        power = self.panel._get_laser_power_mw()
        if not math.isfinite(power) or power <= 0:
            raise ValueError("Laser power must be a positive finite value")
        directory = Path(self.export_directory.text().strip())
        if not str(self.export_directory.text()).strip():
            raise ValueError("An export directory is required")
        if any((directory / f"{self.campaign.campaign_id}.{suffix}").exists() for suffix in ("jsonl", "json", "csv")):
            raise FileExistsError("A campaign with this ID already exists")
        directory.mkdir(parents=True, exist_ok=True)
        # Verify durable output writes and atomic rename before any CT400
        # acquisition, without creating the campaign's first JSONL file.
        probe_path = None
        renamed_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=directory, prefix=".ct400-eta-preflight-", delete=False
            ) as probe:
                probe_path = Path(probe.name)
                probe.write(b"IOPanel CT400 ETA preflight\n")
                probe.flush()
                os.fsync(probe.fileno())
            renamed_path = probe_path.with_name(probe_path.name + ".renamed")
            os.replace(probe_path, renamed_path)
        finally:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
            if renamed_path is not None:
                renamed_path.unlink(missing_ok=True)
        self._power_mw = power
        self._laser_input = self.window._ct400_connected_laser_input
        self._device = self.window.ct400_device

    def _start(self) -> None:
        if self.state != "ready":
            return
        try:
            self._validate_preflight()
        except (ValueError, OSError) as exc:
            self._status(f"Preflight blocked: {exc}")
            return
        question = QMessageBox.question(
            self,
            "Confirm physical CT400 campaign",
            "Start a supervised REAL CT400 scan campaign? Your laser and optical setup must remain "
            "in their approved safe state. Existing scans use the normal laser commands. "
            "Case F uses four detectors only if explicitly confirmed. "
            "The 5 nm/s block requires operator Disconnect/Connect and a new confirmation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if question != QMessageBox.StandardButton.Yes:
            return
        self._original_fields = {
            "start": self.panel.initial_wl.text(),
            "end": self.panel.final_wl.text(),
            "resolution": self.panel.resolution.text(),
            "speed": self.panel.motor_speed.text(),
            "power": self.panel.laser_power.text(),
            "power_unit": self.panel.power_unit.currentText(),
            "input_port": self.panel.input_port.currentData(),
            "detectors": tuple(self.panel._selected_scan_detectors()),
        }
        try:
            self.panel.reserve_calibration(self)
            self.window.histogram_control.set_calibration_blocked(True)
        except RuntimeError as exc:
            self._status(f"Unable to reserve CT400: {exc}")
            return
        self._owns_panel = True
        self.campaign.settings.update(
            {
                "laser_input": self._laser_input.name,
                "entered_power_mw": self._power_mw,
                "export_directory": str(Path(self.export_directory.text().strip())),
                "case_f_approved": self.confirm_extra.isChecked(),
                "simulated": False,
            }
        )
        self.state = "running"
        self._speed_checkpoint_approved = float(self.runs[0].speed_block_nm_s)
        self._refresh_controls()
        self._dispatch_next()

    def _dispatch_next(self) -> None:
        if self.state != "running" or not self._owns_panel or self.active_run is not None:
            return
        if not self.confirm_safe.isChecked():
            self.state = "paused"
            self._status("Optical readiness confirmation withdrawn; recheck before continuing.")
            self._refresh_controls()
            return
        if self.next_index >= len(self.runs):
            self._finish("completed")
            return
        run = self.runs[self.next_index]
        if run.speed_block_nm_s != self._speed_checkpoint_approved:
            self.state = "checkpoint"
            self.confirm_safe.setChecked(False)
            self._status(
                "Speed-block checkpoint. Stop further acquisition. Use the NORMAL CT400 Disconnect, "
                "independently verify safe state, set next Connect speed to 5 nm/s here, "
                "Connect normally, then confirm the reconnected speed and readiness."
            )
            self._refresh_controls()
            return
        if not self._physical_ready(run.speed_block_nm_s):
            self._finish("failed", "CT400 identity, connected input, ownership or Connect speed changed")
            return
        if set(run.detectors) != {"DE_1"} and not self.confirm_extra.isChecked():
            self._record(run, "skipped", None, "DE2–DE4 wiring not confirmed; case F deliberately skipped")
            if self.state == "failed":
                return
            self.next_index += 1
            QTimer.singleShot(0, self._dispatch_next)
            return
        self.active_run = run
        self._timing = None
        self.state = "in_run"
        self._refresh_controls()
        try:
            self.panel.run_calibration_scan(self, run=run, laser_power_mw=self._power_mw, laser_input=self._laser_input)
        except Exception as exc:  # noqa: BLE001  # Unexpected dispatch failure must not schedule later physical scans.
            if self.panel.scan_thread is not None or self.panel.scanning:
                self._stop_requested = True
                self.state = "stopping"
                self.panel._stop_scan(cancelled=True)
                self._status(f"Dispatch failed after worker creation; Stop requested: {exc}")
            else:
                self._finish("failed", f"Scan dispatch failed before worker ownership: {exc}")

    def _receive_timing(self, summary: object) -> None:
        if self.active_run is None or not isinstance(summary, dict):
            return
        if summary.get("scan_id") == self.active_run.run_id:
            self._timing = dict(summary)
        else:
            self._stop_requested = True
            self._status("Unexpected scan ID received; campaign will halt after worker cleanup")

    def _scan_operation_finished(self) -> None:
        if self.active_run is None:
            return
        # A queued callback on the GUI thread checks ownership after the panel
        # has cleaned up its worker and MainWindow has released CT400 SCANNING.
        QTimer.singleShot(0, self._finalize_active_run)

    def _finalize_active_run(self) -> None:
        run = self.active_run
        if run is None:
            return
        if (
            self.panel.scanning
            or self.panel.scan_thread is not None
            or self.window._ct400_operation_state.name != "IDLE"
        ):
            self._stop_requested = True
            self._status("Waiting for CT400 worker cleanup/operation ownership")
            QTimer.singleShot(100, self._finalize_active_run)
            return
        summary = self._timing
        if summary is None or summary.get("scan_id") != run.run_id:
            outcome = "failed"
            reason = "Matching scan timing summary missing after terminal worker completion"
        else:
            raw_outcome = summary.get("outcome")
            if raw_outcome == "success":
                outcome, reason = "success", ""
            elif raw_outcome == "warning":
                outcome, reason = "warning", "CT400 worker reported a warning; operator review required"
            elif raw_outcome == "cancellation":
                outcome, reason = "cancelled", "CT400 worker reported cancellation"
            else:
                outcome, reason = "failed", f"CT400 scan worker outcome: {raw_outcome!r}"
            if summary.get("cleanup_failed") or summary.get("failed_stages"):
                outcome, reason = "failed", "CT400 cleanup or acquisition stage failure"
        self._record(run, outcome, summary, reason)
        self.active_run = None
        self._timing = None
        if self.state == "failed":
            return
        self.next_index += 1
        if self._stop_requested:
            self._finish("stopped")
        elif outcome in ("failed", "cancelled"):
            self._finish("failed" if outcome == "failed" else "stopped", reason)
        elif outcome == "warning":
            self.state = "paused"
            self._status("CT400 warning recorded. Review the instrument before manually continuing.")
            self._refresh_controls()
        elif self.state == "pausing" or not self.auto_next.isChecked():
            self.state = "paused"
            self._status("Run recorded. Press Continue next run after checking the setup.")
            self._refresh_controls()
        else:
            self.state = "running"
            self._refresh_controls()
            self._next_timer.start(self._INTER_RUN_DELAY_MS)

    def _record(self, run: CalibrationRun, outcome: str, timing: dict | None, reason: str) -> None:
        record = {
            **asdict(run),
            "outcome": outcome,
            "simulated": False,
            "laser_input": self._laser_input.name if self._laser_input is not None else None,
            "entered_power_mw": self._power_mw,
            "configured_connect_speed_nm_s": self.window._ct400_connected_speed_nm_s,
            "gui_start_to_completion_seconds": (
                timing.get("gui_start_to_completion_seconds") if timing is not None else None
            ),
            "gui_completion_dispatch_latency_seconds": (
                timing.get("gui_completion_dispatch_latency_seconds") if timing is not None else None
            ),
            "stage_seconds": timing.get("stage_seconds", {}) if timing is not None else {},
            "worker_duration_seconds": timing.get("worker_duration_seconds") if timing is not None else None,
            "returned_sample_count": timing.get("returned_sample_count") if timing is not None else None,
            "failed_stages": timing.get("failed_stages", []) if timing is not None else [],
            "cleanup_failed": timing.get("cleanup_failed") if timing is not None else None,
            "raw_result_code": timing.get("raw_result_code") if timing is not None else None,
            "result_message": timing.get("result_message") if timing is not None else None,
            "terminal_result_kind": timing.get("terminal_result_kind") if timing is not None else None,
            "failure_stage": timing.get("failure_stage") if timing is not None else None,
            "failure_type": timing.get("failure_type") if timing is not None else None,
            "stop_failed": timing.get("stop_failed") if timing is not None else None,
            "reason": reason,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            append_calibration_record(Path(self.export_directory.text().strip()), self.campaign, record)
        except Exception as exc:  # Fail closed if any journal or report write fails.
            logger.exception("Supervised CT400 calibration journal failure")
            self._finish("failed", f"Journal write failed; inspect JSONL before any new scan: {exc}")

    def _continue(self) -> None:
        if self.state != "paused" or self.active_run is not None:
            return
        if not self.confirm_safe.isChecked():
            self._status("Reconfirm the approved optical setup before continuing")
            return
        self.state = "running"
        self._dispatch_next()

    def _pause(self) -> None:
        if self.state == "running":
            self._next_timer.stop()
            self.state = "paused"
        elif self.state == "in_run":
            self.state = "pausing"
        self._status("Campaign paused; current ScanWorker is not interrupted")
        self._refresh_controls()

    def _stop(self) -> None:
        if self.state in ("completed", "failed", "stopped"):
            return
        self._next_timer.stop()
        self._stop_requested = True
        if self.active_run is not None:
            self.state = "stopping"
            self.panel._stop_scan(cancelled=True)  # Existing ScanWorker cancellation; no direct DLL call.
            self._status("Stop requested. Waiting for worker cleanup; no later runs will start.")
            self._refresh_controls()
        else:
            self._finish("stopped")

    def request_shutdown(self) -> None:
        """Cancel dispatch and defer parent close until journal and worker release."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._stop()

    def _prepare_initial_connect_speed(self) -> None:
        if self.state != "ready":
            return
        if (
            self.window._ct400_operation_state.name != "IDLE"
            or self.window._ct400_connected_laser_input is not None
            or self.window._ct400_connection_configured
            or self.window._ct400_scan_state_uncertain
        ):
            self._status("Disconnect normally and confirm optical safe state before setting the next Connect speed")
            return
        answer = QMessageBox.question(
            self,
            "Prepare initial 10 nm/s connection",
            "The CT400 is disconnected. Set the next ordinary Connect command to 10 nm/s "
            "(in-memory configuration only, no instrument call)? Follow the laboratory "
            "safe-state procedure before using the normal Connect action.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.window.config.scan_defaults.speed_nm_s = 10
            self._status("Next Connect configured to 10 nm/s, not instrument readback. Connect normally.")

    def _prepare_next_connect_speed(self) -> None:
        if self.state != "checkpoint":
            return
        if (
            self.window._ct400_operation_state.name != "IDLE"
            or self.window._ct400_connected_laser_input is not None
            or self.window._ct400_connection_configured
            or self.window._ct400_scan_state_uncertain
            or self.window._ct400_recovery_disconnect_completed
        ):
            self._status("Use ordinary Disconnect and independently confirm safe state before changing Connect speed")
            return
        answer = QMessageBox.question(
            self,
            "Prepare operator-controlled speed change",
            "The CT400 is disconnected. Set the application's NEXT ordinary CT400 Connect speed to 5 nm/s? "
            "This modifies the in-memory configuration only; it does not issue a hardware command. "
            "Independently confirm optical safe state, then use the normal Connect action.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.window.config.scan_defaults.speed_nm_s = 5
        self._status("Next Connect configured to 5 nm/s (not instrument readback). Use normal Connect, then confirm.")

    def _acknowledge_speed_checkpoint(self) -> None:
        if self.state != "checkpoint" or not self.confirm_safe.isChecked():
            self._status("Fresh operator optical safety confirmation required for the new speed block")
            return
        run = self.runs[self.next_index]
        if not self._physical_ready(run.speed_block_nm_s):
            self._status("Not ready: ordinary Connect must succeed at the new configured speed and same input")
            return
        answer = QMessageBox.question(
            self,
            "Approve reconnected speed block",
            f"Confirm supervised CT400 connection at requested {run.speed_block_nm_s:g} nm/s "
            "(connection command value, not device readback), with independently verified optical limits, "
            "power and detector wiring. Continue the remaining supervised runs?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._speed_checkpoint_approved = run.speed_block_nm_s
        self.state = "running"
        self._dispatch_next()

    def _finish(self, state: str, reason: str = "") -> None:
        self._next_timer.stop()
        self.state = state
        if self._owns_panel and self.panel.scan_thread is None and not self.panel.scanning:
            try:
                self.panel.release_calibration(self)
            except RuntimeError:
                logger.exception("Unable to release CT400 calibration scan reservation")
                self._status("CT400 calibration reservation release failed; shutdown deferred.")
                return
            self.window.histogram_control.set_calibration_blocked(False)
            self._owns_panel = False
            self._restore_fields()
        self._status(
            f"Campaign {state}: {self.next_index}/{len(self.runs)} results recorded. "
            f"{reason} Files: {self.export_directory.text()}"
        )
        self._refresh_controls()
        if self._close_after_stop and not self._owns_panel:
            QTimer.singleShot(0, self.close)
        if self._shutdown_requested and not self._owns_panel:
            QTimer.singleShot(0, self.window.close)

    def _restore_fields(self) -> None:
        fields = self._original_fields
        if fields is None:
            return
        self.panel.initial_wl.setText(fields["start"])
        self.panel.final_wl.setText(fields["end"])
        self.panel.resolution.setText(fields["resolution"])
        self.panel.motor_speed.setText(fields["speed"])
        self.panel.laser_power.setText(fields["power"])
        self.panel.power_unit.setCurrentText(fields["power_unit"])
        index = self.panel.input_port.findData(fields["input_port"])
        if index >= 0:
            self.panel.input_port.setCurrentIndex(index)
        for detector, checkbox in self.panel.scan_detector_cbs.items():
            checkbox.setChecked(detector in fields["detectors"])

    def _status(self, message: str) -> None:
        self.progress.setText(message)
        logger.info("CT400 calibration: %s", message)

    def _refresh_controls(self, *_args) -> None:
        self.start_button.setEnabled(self.state == "ready" and self.confirm_safe.isChecked())
        self.continue_button.setEnabled(self.state == "paused" and self.confirm_safe.isChecked())
        self.pause_button.setEnabled(self.state in ("running", "in_run"))
        self.stop_button.setEnabled(self.state not in ("ready", "completed", "failed", "stopped"))
        self.prepare_initial_speed_button.setEnabled(self.state == "ready")
        self.prepare_speed_button.setEnabled(self.state == "checkpoint")
        self.ack_speed_button.setEnabled(self.state == "checkpoint" and self.confirm_safe.isChecked())
        self.export_directory.setEnabled(self.state == "ready")
        self.confirm_extra.setEnabled(self.state == "ready")
        self.auto_next.setEnabled(self.state not in ("in_run", "stopping", "completed", "failed", "stopped"))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._owns_panel:
            event.ignore()
            self._close_after_stop = True
            self._stop()
            return
        self._next_timer.stop()
        super().closeEvent(event)
