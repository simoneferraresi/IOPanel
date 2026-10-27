"""M4-E2B: Qt-owner synchronized acquisition, strictly Dummy instruments.

A PM5020 QThread owns every PM backend call. A dedicated CT400 scan QThread
owns all CT scan commands. This is NOT physical-trigger qualification.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from hardware.ct400_types import CT400ScanResultKind, Enable
from hardware.dummy_ct400 import DummyCT400
from hardware.pm5020_types import BurstAcquisitionConfig
from logic.simulated_synchronized_acquisition import (
    SimulatedSynchronizedCapture,
    SimulatedSyncSettings,
    synthetic_ct400_sync_text,
)
from logic.simulated_synchronized_archive import archive_simulated_synchronized_capture
from logic.synchronized_owner_leases import OwnerRequest
from logic.trigger_sync_alignment import align_ct400_burst, parse_ct400_sync_text

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("LabApp.shg_simulation")


class SimulatedCT400SyncWorker(QObject):
    """Never accepts a physical handle; completion follows sync-output cleanup."""

    completed = Signal(object, str)

    def __init__(self, device: DummyCT400, settings: SimulatedSyncSettings) -> None:
        super().__init__()
        if not isinstance(device, DummyCT400):
            raise TypeError("CT synchronized worker is restricted to DummyCT400")
        self.device = device
        self.settings = settings
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()
        # The sole documented concurrent stop/wait pattern, simulation ONLY.
        if self.device._is_scanning:
            self.device.stop_scan()

    @Slot()
    def run(self) -> None:
        result = None
        error = ""
        enabled = False
        started = False
        try:
            if self.cancel_event.is_set():
                raise RuntimeError("Cancelled before CT setup")
            settings = self.settings
            self.device.set_sampling_res(settings.resolution_pm)
            selected = set(settings.detectors)
            from hardware.ct400_types import Detector

            self.device.set_detector_array(
                Enable.ENABLE if Detector.DE_2 in selected else Enable.DISABLE,
                Enable.ENABLE if Detector.DE_3 in selected else Enable.DISABLE,
                Enable.ENABLE if Detector.DE_4 in selected else Enable.DISABLE,
                Enable.DISABLE,
            )
            self.device.set_scan(settings.laser_power_mw, settings.start_nm, settings.stop_nm)
            self.device.set_external_synchronization(Enable.ENABLE)
            enabled = True
            if self.cancel_event.is_set():
                raise RuntimeError("Cancelled before CT scan start")
            self.device.start_scan()
            started = True
            if self.cancel_event.is_set():
                self.device.stop_scan()
            status = self.device.scan_wait_end()
            started = False
            if self.cancel_event.is_set() or status.kind is CT400ScanResultKind.USER_CANCELLED:
                raise RuntimeError("Synchronized CT scan cancelled")
            if status.kind not in (CT400ScanResultKind.SUCCESS, CT400ScanResultKind.WARNING):
                raise RuntimeError(f"CT scan returned vendor code {status.raw_code}: {status.error_message}")
            wavelengths, detector_data = self.device.get_data_points(list(settings.detectors))
            sync_text = synthetic_ct400_sync_text(start_nm=settings.start_nm, stop_nm=settings.stop_nm)
            result = (
                status,
                tuple(float(value) for value in wavelengths),
                tuple(tuple(float(value) for value in channel) for channel in detector_data),
                sync_text,
            )
        except Exception as exc:  # noqa: BLE001 - retain partial native outcome as a failed run.
            error = str(exc)
            if started:
                try:
                    self.device.stop_scan()
                    self.device.scan_wait_end()
                except Exception as cleanup_exc:  # noqa: BLE001 - refuse confirmed cleanup.
                    error += f"; CT stop/wait cleanup failed: {cleanup_exc}"
        finally:
            if enabled:
                try:
                    self.device.set_external_synchronization(Enable.DISABLE)
                except Exception as cleanup_exc:  # noqa: BLE001 - never report an unsafe cleanup as success.
                    error += f"; CT sync disable failed: {cleanup_exc}"
            self.completed.emit(None if error else result, error)


class SimulatedSHGPanel(QWidget):
    """Complete simulator-only tab with ownership/recovery mediated by Qt workers."""

    finished = Signal(bool, str)

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.host = window
        self.setObjectName("simulatedSHGTab")
        self._generation = 0
        self._token: int | None = None
        self._pm_generation: int | None = None
        self._request: OwnerRequest | None = None
        self._settings: SimulatedSyncSettings | None = None
        self._destination: Path | None = None
        self._ct_thread: QThread | None = None
        self._ct_worker: SimulatedCT400SyncWorker | None = None
        self._ct_thread_stopped = True
        self._ct_result: tuple | None = None
        self._ct_error = ""
        self._ct_ack = False
        self._pm_ack = False
        self._pm_quiesced = False
        self._pm_cleanup_requested = False
        self._pm_data = None
        self._failure = ""
        self._cancelled = False
        self._timed_out = False
        self._closing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self._on_timeout)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("SHG synchronized acquisition — SIMULATION ONLY. Physical CT400/PM5020 and EDFA are never operated.")
        )
        layout.addWidget(QLabel("Timing unqualified; PM ticks are raw uint32. Zero-based alignment is provisional."))
        folder = QHBoxLayout()
        self.output_path = QLineEdit(self)
        self.output_path.setPlaceholderText("Choose a directory for checksum-linked simulated archives")
        self.browse_button = QPushButton("Browse…", self)
        self.browse_button.clicked.connect(self._browse)
        folder.addWidget(self.output_path)
        folder.addWidget(self.browse_button)
        layout.addLayout(folder)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start simulated synchronized SHG capture", self)
        self.start_button.setObjectName("shgSimulatedStartButton")
        self.stop_button = QPushButton("Cancel", self)
        self.stop_button.setObjectName("shgSimulatedCancelButton")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_capture)
        self.stop_button.clicked.connect(self.cancel)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.status_label = QLabel("Idle — simulated hardware only", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        worker = self.host.pm5020_panel.worker
        worker.synchronized_quiesced.connect(self._on_pm_quiesced, Qt.ConnectionType.QueuedConnection)
        worker.synchronized_armed.connect(self._on_pm_armed, Qt.ConnectionType.QueuedConnection)
        worker.synchronized_data.connect(self._on_pm_data, Qt.ConnectionType.QueuedConnection)
        worker.synchronized_cleaned.connect(self._on_pm_cleaned, Qt.ConnectionType.QueuedConnection)

    @property
    def active(self) -> bool:
        return self._token is not None

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose simulated SHG archive directory")
        if directory:
            self.output_path.setText(directory)

    @Slot()
    def start_capture(
        self, *, settings: SimulatedSyncSettings | None = None, destination_root: str | Path | None = None
    ) -> None:
        if self.active or self.host._shutdown_requested:
            return
        panel = self.host.pm5020_panel
        device = self.host.ct400_device
        if not isinstance(device, DummyCT400) or panel.backend_mode != "simulation":
            self.status_label.setText("Refused: both instrument backends must be simulated.")
            return
        if not self.host._ct400_lease_available_for_new_operation():
            self.status_label.setText("Refused: CT400 is busy, recovering, or not authorized.")
            return
        campaign = self.host._ct400_campaign_dialog
        if campaign is not None and campaign.active:
            self.status_label.setText("Refused: supervised calibration owns CT400.")
            return
        from hardware.edfa_types import EDFAState

        if self.host.edfa_panel is not None and self.host.edfa_panel._backend_state is not EDFAState.DISCONNECTED:
            self.status_label.setText("Refused: disconnect EDFA before simulated synchronized acquisition.")
            return
        destination = Path(destination_root) if destination_root is not None else Path(self.output_path.text())
        if not str(destination).strip() or (destination_root is None and not self.output_path.text().strip()):
            self.status_label.setText("Choose an archive directory before starting.")
            return
        self._generation += 1
        token = self._generation
        try:
            generation = panel.begin_synchronized_handoff(token)
        except RuntimeError as exc:
            self.status_label.setText(f"PM5020 handoff refused: {exc}")
            return
        self._token = token
        self._pm_generation = generation
        self._request = None
        self._settings = settings or SimulatedSyncSettings()
        self._destination = destination
        self._ct_result = None
        self._ct_error = ""
        self._ct_ack = False
        self._pm_ack = False
        self._pm_quiesced = False
        self._pm_cleanup_requested = False
        self._pm_data = None
        self._failure = ""
        self._cancelled = False
        self._timed_out = False
        self._ct_thread_stopped = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText("Waiting for PM owner-thread monitoring quiescence…")
        self._timer.start()

    def _matches(self, token: int, generation: int) -> bool:
        return self._token == token and self._pm_generation == generation

    @Slot(int, int, bool, str)
    def _on_pm_quiesced(self, token: int, generation: int, ok: bool, reason: str) -> None:
        if not self._matches(token, generation):
            return
        if not ok:
            self._failure = f"PM owner handoff not confirmed: {reason}"
            self._finalize()
            return
        self._pm_quiesced = True
        if self._cancelled:
            self._fail("Cancelled during PM handoff")
            return
        try:
            if not self.host._ct400_lease_available_for_new_operation():
                raise RuntimeError("CT400 was claimed while PM handoff was pending")
            protocol = self.host.synchronized_owner_leases
            req = protocol.reserve(ct400_generation=token, pm5020_generation=generation)
            self._request = req
            # Project the combined lease into existing CT controls without
            # forging a second CT400OperationState/lease.
            self.host.control_panel.set_ct400_operation_state("SYNCHRONIZED")
            self.host.histogram_control.set_ct400_operation_state("SYNCHRONIZED")
            self.host.alignment_tab.set_ct400_operation_state("SYNCHRONIZED")
            self.host.ct400_connect_action.setEnabled(False)
            protocol.acknowledge_pm_quiesced(req, generation=generation)
            protocol.acknowledge_ct400_ready(req, generation=token)
            self.host.pm5020_panel.sync_arm_requested.emit(token, generation, req.lease, BurstAcquisitionConfig())
            self.status_label.setText("Both resources atomically reserved; awaiting PM arm acknowledgement…")
        except Exception as exc:  # noqa: BLE001 - an invalid lease is never permission to scan.
            self._fail(f"Synchronized reservation failed: {exc}")

    @Slot(int, int, bool, str)
    def _on_pm_armed(self, token: int, generation: int, ok: bool, reason: str) -> None:
        if not self._matches(token, generation):
            return
        if not ok or self._cancelled:
            self._fail(reason or "Cancelled before CT scan")
            return
        request = self._request
        if request is None:
            self._fail("Missing atomic lease before CT start")
            return
        protocol = self.host.synchronized_owner_leases
        if not protocol.acknowledge_pm_armed(request, generation=generation) or not protocol.may_start_ct400(request):
            self._fail("CT start denied: PM arm or lease acknowledgement missing")
            return
        try:
            device = self.host.ct400_device
            if not isinstance(device, DummyCT400) or self._settings is None:
                raise RuntimeError("CT device changed after reservation")
            worker = SimulatedCT400SyncWorker(device, self._settings)
            thread = QThread(self)
            self._ct_worker = worker
            self._ct_thread = thread
            self._ct_thread_stopped = False
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self._on_ct_result, Qt.ConnectionType.QueuedConnection)
            worker.completed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(self._on_ct_thread_finished, Qt.ConnectionType.QueuedConnection)
            thread.start()
            self.status_label.setText("PM armed; simulated CT400 synchronized scan running…")
        except Exception as exc:  # noqa: BLE001 - dispatch failure is not a successful CT scan.
            self._fail(f"CT scan dispatch failed: {exc}")

    @Slot(object, str)
    def _on_ct_result(self, result: object, error: str) -> None:
        if not self.active:
            return
        self._ct_result = result if isinstance(result, tuple) else None
        self._ct_error = error
        self._on_ct_completion_ready()

    @Slot()
    def _on_ct_thread_finished(self) -> None:
        self._ct_thread_stopped = True
        self._ct_thread = None
        self._ct_worker = None
        self._on_ct_completion_ready()

    def _on_ct_completion_ready(self) -> None:
        if not self.active or not self._ct_thread_stopped or self._ct_ack:
            return
        if self._ct_result is None and not self._ct_error:
            return
        request = self._request
        if request is None:
            return
        self.host.synchronized_owner_leases.acknowledge_ct400_complete(request, generation=request.ct400_generation)
        self._ct_ack = True
        if self._ct_error or self._cancelled:
            self._fail(self._ct_error or "CT scan cancelled")
        elif self._pm_generation is not None and self._token is not None:
            self.host.pm5020_panel.sync_retrieve_requested.emit(self._token, self._pm_generation)

    @Slot(int, int, object, str)
    def _on_pm_data(self, token: int, generation: int, data: object, reason: str) -> None:
        if not self._matches(token, generation):
            return
        if reason or data is None or self._cancelled:
            self._fail(reason or "PM retrieval cancelled or empty")
            return
        self._pm_data = data
        self._request_pm_cleanup()

    def _request_pm_cleanup(self) -> None:
        if self._pm_cleanup_requested or not self.active:
            return
        self._pm_cleanup_requested = True
        self.host.pm5020_panel.sync_cleanup_requested.emit(self._token, self._pm_generation)

    def _fail(self, reason: str) -> None:
        self._failure = self._failure or reason
        self.status_label.setText(f"Synchronized acquisition aborted: {self._failure}")
        if self._ct_worker is not None and not self._ct_thread_stopped:
            self._ct_worker.cancel()
            return
        if not self._pm_quiesced:
            # A queued owner-thread close is still outstanding; never send a
            # competing cleanup request ahead of its acknowledgment.
            return
        if self._request is not None and not self._ct_ack:
            self.host.synchronized_owner_leases.acknowledge_ct400_complete(
                self._request, generation=self._request.ct400_generation
            )
            self._ct_ack = True
        self._request_pm_cleanup()

    @Slot(int, int, bool, str)
    def _on_pm_cleaned(self, token: int, generation: int, ok: bool, reason: str) -> None:
        if not self._matches(token, generation):
            return
        request = self._request
        if not ok:
            self._failure = self._failure or f"PM cleanup not confirmed: {reason}"
            if request is not None:
                self.host.synchronized_owner_leases.quarantine(request, self._failure)
        else:
            self._pm_ack = True
            if request is not None:
                self.host.synchronized_owner_leases.acknowledge_pm_complete(request, generation=generation)
        if not self._ct_ack and request is not None and self._ct_thread_stopped:
            self.host.synchronized_owner_leases.acknowledge_ct400_complete(request, generation=request.ct400_generation)
            self._ct_ack = True
        self._finalize()

    def _finalize(self) -> None:
        request = self._request
        success = bool(
            not self._failure
            and not self._cancelled
            and not self._timed_out
            and self._pm_data is not None
            and self._ct_result is not None
        )
        message = self._failure or ("Synchronized simulation completed" if success else "Synchronized run cancelled")
        if request is not None:
            try:
                if self._timed_out:
                    self.host.synchronized_owner_leases.quarantine(request, "Synchronized owner response timeout")
                if self._ct_ack and self._pm_ack and not self._timed_out:
                    self.host.synchronized_owner_leases.release_after_cleanup(request)
                else:
                    self.host.synchronized_owner_leases.quarantine(request, message)
                    success = False
            except Exception as exc:  # noqa: BLE001 - quarantine never authorizes another run.
                logger.error("Synchronized lease cannot be released: %s", exc)
                success = False
                message = f"Recovery required: {exc}"
        if success:
            try:
                if self._settings is None or self._ct_result is None or self._destination is None:
                    raise RuntimeError("Missing synchronized capture state")
                status, wavelengths, detector_data, sync_text = self._ct_result
                pulses = parse_ct400_sync_text(sync_text)
                aligned = align_ct400_burst(pulses, self._pm_data, index_base=0)
                from datetime import UTC, datetime

                capture = SimulatedSynchronizedCapture(
                    settings=self._settings,
                    status=status,
                    pm_burst=self._pm_data,
                    sync_text=sync_text,
                    pulses=pulses,
                    aligned=aligned,
                    ct400_wavelengths_nm=wavelengths,
                    ct400_detector_data=detector_data,
                    completed_at_utc=datetime.now(UTC),
                )
                path = archive_simulated_synchronized_capture(capture, self._destination)
                message = f"Simulated SHG capture archived to {path}"
            except Exception as exc:  # noqa: BLE001 - never report a partial archive as success.
                success = False
                message = f"Archive failed: {exc}"
        self._timer.stop()
        self.host.pm5020_panel.finish_synchronized_handoff()
        self.host.control_panel.set_ct400_operation_state(self.host._ct400_operation_state.name)
        self.host.histogram_control.set_ct400_operation_state(self.host._ct400_operation_state.name)
        self.host.alignment_tab.set_ct400_operation_state(self.host._ct400_operation_state.name)
        self.host._update_ct400_retry_action()
        self._token = None
        self._pm_generation = None
        self._request = None
        self._ct_result = None
        self._pm_data = None
        self.start_button.setEnabled(not self._closing)
        self.stop_button.setEnabled(False)
        self.status_label.setText(message)
        self.finished.emit(success, message)
        if self._closing and not self.host._shutdown_complete:
            QTimer.singleShot(0, self.host, self.host.close)

    @Slot()
    def cancel(self) -> None:
        if not self.active:
            return
        self._cancelled = True
        if self._request is not None:
            self.host.synchronized_owner_leases.cancel(self._request)
        self._fail("Operator requested cancellation")

    def request_shutdown(self) -> None:
        if not self.active:
            return
        self._closing = True
        self.cancel()

    @Slot()
    def _on_timeout(self) -> None:
        if not self.active:
            return
        self._timed_out = True
        if self._request is not None:
            self.host.synchronized_owner_leases.quarantine(self._request, "Synchronized owner response timeout")
        self._fail("Owner-thread response timed out; resources quarantined")
