"""M4-E1: deterministic, hardware-free coordinated scan regression tests."""

from __future__ import annotations

import hashlib
import json
import threading

import pytest

from hardware.ct400_types import CT400ScanResultKind, Detector, Enable, LaserInput, LaserSource
from hardware.dummy_ct400 import DummyCT400
from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020_types import PM5020State
from logic.pm5020_capture_audit import audit_pm5020_burst_run
from logic.simulated_synchronized_acquisition import (
    SimulatedSynchronizedCoordinator,
    SimulatedSyncSettings,
    SynchronizedAcquisitionCancelled,
    SynchronizedAcquisitionError,
    synthetic_ct400_sync_text,
)
from logic.simulated_synchronized_archive import archive_simulated_synchronized_capture
from logic.trigger_sync_alignment import TriggerSyncError


def _connected_pair(
    *,
    ct: DummyCT400 | None = None,
    pm: DummyPM5020 | None = None,
) -> tuple[DummyCT400, DummyPM5020]:
    ct = ct or DummyCT400(scan_duration=0)
    pm = pm or DummyPM5020(burst_sample_count=257)
    ct.set_laser(LaserInput.LI_1, Enable.DISABLE, 1, LaserSource.LS_TunicsPlus, 1545, 1555, 10)
    pm.connect()
    return ct, pm


def _coordinator(
    ct: DummyCT400,
    pm: DummyPM5020,
    *,
    sync_text: str | None = None,
    settings: SimulatedSyncSettings | None = None,
) -> SimulatedSynchronizedCoordinator:
    return SimulatedSynchronizedCoordinator(
        ct, pm, lambda: sync_text if sync_text is not None else synthetic_ct400_sync_text(), settings=settings
    )


def test_simulated_capture_preserves_native_data_and_ct400_processed_points():
    ct, pm = _connected_pair()
    capture = _coordinator(ct, pm).run()
    assert capture.simulated is True
    assert capture.index_base == 0
    assert capture.timing_qualified is False
    assert capture.status.kind is CT400ScanResultKind.SUCCESS
    assert capture.pm_burst.sample_count == 257
    assert len(capture.pulses) == 240
    assert capture.aligned.buffer_indices == tuple(range(14, 254))
    assert capture.aligned.timestamps_ticks[0] == 14000
    assert capture.aligned.timestamps_ticks[-1] == 253000
    assert capture.aligned.power_ch1_w[0] == capture.pm_burst.power_ch1_w[14]
    assert len(capture.ct400_wavelengths_nm) == 201
    assert len(capture.ct400_detector_data) == 1
    assert len(capture.ct400_detector_data[0]) == 201
    assert ct.external_synchronization_calls == [Enable.ENABLE, Enable.DISABLE]
    assert pm.state is PM5020State.IDLE
    assert ct.stop_scan_calls == 0
    assert capture.completed_at_utc.utcoffset() is not None


def test_pm_armed_before_ct_scan_and_retrieved_after_scan():
    events = []

    class RecordingCT(DummyCT400):
        def start_scan(self):
            events.append("ct.start")
            super().start_scan()

        def scan_wait_end(self):
            events.append("ct.wait")
            return super().scan_wait_end()

    class RecordingPM(DummyPM5020):
        def start_burst(self):
            events.append("pm.arm")
            super().start_burst()

        def retrieve_burst(self, **kwargs):
            events.append("pm.retrieve")
            return super().retrieve_burst(**kwargs)

    ct, pm = _connected_pair(ct=RecordingCT(scan_duration=0), pm=RecordingPM(burst_sample_count=257))
    _coordinator(ct, pm).run()
    assert events == ["pm.arm", "ct.start", "ct.wait", "pm.retrieve"]


def test_documented_ct400_warning_is_archived_as_warning_not_success():
    ct, pm = _connected_pair(ct=DummyCT400(scan_duration=0, scan_error="slow scan", scan_result_code=101))
    capture = _coordinator(ct, pm).run()
    assert capture.status.raw_code == 101
    assert capture.status.kind is CT400ScanResultKind.WARNING
    assert capture.status.error_message == "slow scan"
    assert pm.is_ready


def test_fatal_ct400_outcome_cleans_armed_pm_without_retrieval():
    ct, pm = _connected_pair(ct=DummyCT400(scan_duration=0, scan_error="DSP failure", scan_result_code=2))
    with pytest.raises(SynchronizedAcquisitionError, match="raw code 2"):
        _coordinator(ct, pm).run()
    assert pm.state is PM5020State.DISCONNECTED
    assert ct.external_synchronization_calls == [Enable.ENABLE, Enable.DISABLE]


def test_failed_pm_retrieval_closes_and_requires_reconnection():
    ct, pm = _connected_pair(pm=DummyPM5020(burst_sample_count=257, fail_operations={"retrieve_burst"}))
    with pytest.raises(Exception, match="retrieve_burst"):
        _coordinator(ct, pm).run()
    assert pm.state is PM5020State.DISCONNECTED
    assert ct.external_synchronization_calls[-1] is Enable.DISABLE


def test_alignment_failure_never_claims_timing_qualification():
    ct, pm = _connected_pair()
    malformed = "14 1545\n15 1546\n99 1547\n"
    with pytest.raises(TriggerSyncError, match="skips"):
        _coordinator(ct, pm, sync_text=malformed).run()
    assert ct.external_synchronization_calls[-1] is Enable.DISABLE
    assert pm.state is PM5020State.IDLE  # burst was already retrieved/disarmed


def test_cancel_during_scan_wait_unblocks_simulated_scan_and_cleans_up():
    gate = threading.Event()
    started = threading.Event()

    class BlockingCT(DummyCT400):
        def start_scan(self):
            super().start_scan()
            started.set()

    ct, pm = _connected_pair(ct=BlockingCT(scan_duration=5, wait_gate=gate))
    coordinator = _coordinator(ct, pm)
    outcomes = []

    def execute():
        try:
            coordinator.run()
        except Exception as exc:  # noqa: BLE001 - worker records any failure for assertions
            outcomes.append(exc)

    thread = threading.Thread(target=execute)
    thread.start()
    assert started.wait(timeout=2)
    coordinator.cancel()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], SynchronizedAcquisitionCancelled)
    assert ct.stop_scan_calls >= 1
    assert ct.external_synchronization_calls[-1] is Enable.DISABLE
    assert pm.state is PM5020State.DISCONNECTED


def test_concurrent_run_is_rejected_without_starting_second_scan():
    gate = threading.Event()
    started = threading.Event()

    class BlockingCT(DummyCT400):
        def start_scan(self):
            super().start_scan()
            started.set()

    ct, pm = _connected_pair(ct=BlockingCT(scan_duration=5, wait_gate=gate))
    coordinator = _coordinator(ct, pm)
    finished = []

    def execute():
        try:
            coordinator.run()
        except SynchronizedAcquisitionCancelled:
            finished.append("cancelled")

    thread = threading.Thread(target=execute)
    thread.start()
    assert started.wait(timeout=2)
    with pytest.raises(SynchronizedAcquisitionError, match="already running"):
        coordinator.run()
    coordinator.cancel()
    thread.join(timeout=2)
    assert finished == ["cancelled"]
    assert ct.scan_wait_end_calls == 1


def test_physical_backends_are_rejected_before_any_device_call():
    ct, pm = _connected_pair()
    with pytest.raises(TypeError, match="physical hardware"):
        SimulatedSynchronizedCoordinator(object(), pm, lambda: "")
    with pytest.raises(TypeError, match="physical hardware"):
        SimulatedSynchronizedCoordinator(ct, object(), lambda: "")


def test_disconnected_preflight_does_not_arm_anything():
    ct = DummyCT400(scan_duration=0)
    pm = DummyPM5020()
    with pytest.raises(SynchronizedAcquisitionError, match="connected"):
        _coordinator(ct, pm).run()
    assert pm.state is PM5020State.DISCONNECTED
    assert ct.external_synchronization_calls == []


@pytest.mark.parametrize(
    "settings",
    [
        SimulatedSyncSettings(),
        SimulatedSyncSettings(detectors=(Detector.DE_1, Detector.DE_3)),
    ],
)
def test_simulated_archive_preserves_raw_files_and_audit_compatibility(tmp_path, settings):
    ct, pm = _connected_pair()
    capture = _coordinator(ct, pm, settings=settings).run()
    archive = archive_simulated_synchronized_capture(capture, tmp_path / "runs")
    assert archive.is_dir()
    metadata = json.loads((archive / "acquisition_metadata.json").read_text(encoding="utf-8"))
    assert metadata["simulated"] is True
    assert metadata["timing_qualified"] is False
    assert metadata["index_base"] == 0
    assert metadata["ct400_scan_result_kind"] == "SUCCESS"
    assert metadata["pm5020_reported_burst_samples"] == 257
    assert metadata["ct400_processed_points"] == 201
    assert len(metadata["files"]) == 3
    assert metadata["ct400_settings"]["detectors"] == [d.value for d in settings.detectors]
    for record in metadata["files"].values():
        path = archive / record["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
    audit = audit_pm5020_burst_run(archive)
    assert audit.pm5020_samples == 257
    assert audit.ct400_pulses == 240
    assert audit.candidates[0].structurally_valid
    assert not audit.timing_qualified


def test_synthetic_sync_fixture_validates_its_own_parameters():
    with pytest.raises(ValueError):
        synthetic_ct400_sync_text(first_index=-1)
    with pytest.raises(ValueError):
        synthetic_ct400_sync_text(point_count=1)
    with pytest.raises(ValueError):
        SimulatedSyncSettings(speed_nm_s=3)
    with pytest.raises(ValueError):
        SimulatedSyncSettings(detectors=(Detector.DE_1, Detector.DE_1))
