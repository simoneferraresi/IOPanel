"""Offline-only lifecycle tests for the Issue #140 calibration simulator."""

import json

import pytest

from logic.ct400_calibration import CalibrationCampaign, restore_campaign_journal
from logic.ct400_calibration_simulation import SimulatedCalibrationCoordinator, SimulationState


def _running(tmp_path):
    coordinator = SimulatedCalibrationCoordinator(tmp_path)
    coordinator.start()
    assert coordinator.state is SimulationState.SPEED_CHECKPOINT
    assert coordinator.pending_speed_nm_s == 10
    coordinator.acknowledge_simulated_speed(10)
    return coordinator


def test_complete_18_run_simulation_observes_speed_boundary_and_durable_records(tmp_path):
    coordinator = _running(tmp_path)
    completed_ids = []
    for index in range(18):
        if index == 15:
            assert coordinator.state is SimulationState.SPEED_CHECKPOINT
            assert coordinator.pending_speed_nm_s == 5
            with pytest.raises(ValueError, match="requires 5"):
                coordinator.acknowledge_simulated_speed(10)
            coordinator.acknowledge_simulated_speed(5)
        assert coordinator.state is SimulationState.RUNNING
        run = coordinator.begin_next_run()
        completed_ids.append(run.run_id)
        coordinator.complete_run(
            run.run_id,
            simulated_gui_duration_seconds=run.nominal_sweep_seconds + 0.5,
            stage_seconds={"scan_wait_end_seconds": run.nominal_sweep_seconds},
        )
    assert coordinator.state is SimulationState.COMPLETED
    assert coordinator.completed_count == 18
    assert coordinator.next_index == 18
    assert coordinator.active_run is None
    assert len(set(completed_ids)) == 18
    assert coordinator.campaign.analysis_summary["successful_measurements"] == 18
    assert [record["run_id"] for record in coordinator.campaign.records] == completed_ids
    assert all(record["simulation_only"] is True for record in coordinator.campaign.records)
    assert [record["speed_block_nm_s"] for record in coordinator.campaign.records] == [10] * 15 + [5] * 3

    journal = tmp_path / f"{coordinator.campaign.campaign_id}.jsonl"
    manifest = tmp_path / f"{coordinator.campaign.campaign_id}.json"
    csv_path = tmp_path / f"{coordinator.campaign.campaign_id}.csv"
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 18
    assert json.loads(manifest.read_text(encoding="utf-8"))["records"] == coordinator.campaign.records
    assert csv_path.read_bytes().startswith(b"\\xef\\xbb\\xbf")
    with pytest.raises(RuntimeError, match="completed"):
        coordinator.begin_next_run()


def test_no_run_overlaps_and_stale_or_duplicate_completion_cannot_advance(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    assert coordinator.state is SimulationState.IN_RUN
    with pytest.raises(RuntimeError, match="in_run"):
        coordinator.begin_next_run()
    with pytest.raises(ValueError, match="Stale"):
        coordinator.complete_run("different-run")
    assert coordinator.completed_count == 0
    coordinator.complete_run(run.run_id)
    assert coordinator.completed_count == 1
    with pytest.raises(RuntimeError, match="running"):
        coordinator.complete_run(run.run_id)
    assert coordinator.completed_count == 1


def test_pause_during_active_fake_run_stops_only_at_completion(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    coordinator.pause()
    assert coordinator.state is SimulationState.IN_RUN
    coordinator.complete_run(run.run_id)
    assert coordinator.state is SimulationState.PAUSED
    assert coordinator.completed_count == 1
    with pytest.raises(RuntimeError, match="paused"):
        coordinator.begin_next_run()
    coordinator.resume()
    assert coordinator.state is SimulationState.RUNNING
    assert coordinator.begin_next_run().case_id == "A"


def test_pause_between_runs_and_invalid_state_transitions(tmp_path):
    coordinator = _running(tmp_path)
    coordinator.pause()
    assert coordinator.state is SimulationState.PAUSED
    with pytest.raises(RuntimeError, match="paused"):
        coordinator.begin_next_run()
    coordinator.resume()
    with pytest.raises(RuntimeError, match="running"):
        coordinator.resume()
    run = coordinator.begin_next_run()
    coordinator.complete_run(run.run_id)
    assert coordinator.state is SimulationState.RUNNING


def test_stop_while_fake_run_in_flight_prevents_subsequent_scheduling(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    coordinator.stop()
    coordinator.stop()
    assert coordinator.state is SimulationState.STOPPING
    with pytest.raises(RuntimeError, match="stopping"):
        coordinator.begin_next_run()
    coordinator.complete_run(run.run_id, outcome="cancelled", reason="simulated user stop")
    assert coordinator.state is SimulationState.STOPPED
    assert coordinator.campaign.records[-1]["outcome"] == "cancelled"
    assert coordinator.completed_count == 1
    with pytest.raises(RuntimeError, match="stopped"):
        coordinator.resume()


@pytest.mark.parametrize("stop_state", ["running", "paused", "checkpoint"])
def test_stop_between_runs_never_creates_a_record(tmp_path, stop_state):
    coordinator = _running(tmp_path)
    if stop_state == "paused":
        coordinator.pause()
    elif stop_state == "checkpoint":
        coordinator = SimulatedCalibrationCoordinator(tmp_path)
        coordinator.start()
    coordinator.stop()
    assert coordinator.state is SimulationState.STOPPED
    assert coordinator.completed_count == 0


def test_abort_inflight_journals_partial_result_and_never_dispatches_next(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    coordinator.abort("fake CT400 disconnect")
    assert coordinator.state is SimulationState.ABORTING
    coordinator.complete_run(run.run_id, outcome="failed")
    assert coordinator.state is SimulationState.ABORTED
    assert coordinator.campaign.records[0]["reason"] == "fake CT400 disconnect"
    assert coordinator.campaign.records[0]["simulation_only"] is True
    with pytest.raises(RuntimeError, match="aborted"):
        coordinator.begin_next_run()


def test_abort_while_idle_requires_reason(tmp_path):
    coordinator = _running(tmp_path)
    with pytest.raises(ValueError, match="requires a reason"):
        coordinator.abort(" ")
    coordinator.abort("simulated preflight rejection")
    assert coordinator.state is SimulationState.ABORTED
    assert coordinator.completed_count == 0


def test_failure_requires_manual_investigation_without_resume(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    coordinator.complete_run(run.run_id, outcome="failed", reason="synthetic timeout")
    assert coordinator.state is SimulationState.FAILED
    assert coordinator.failure_reason == "synthetic timeout"
    with pytest.raises(RuntimeError, match="failed"):
        coordinator.resume()
    assert coordinator.completed_count == 1


def test_warning_halts_for_manual_review_before_next_run(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    coordinator.complete_run(run.run_id, outcome="warning")
    assert coordinator.state is SimulationState.PAUSED
    assert coordinator.campaign.records[0]["outcome"] == "warning"
    coordinator.resume()
    assert coordinator.state is SimulationState.RUNNING


def test_skip_records_reason_without_fake_hardware_execution(tmp_path):
    coordinator = _running(tmp_path)
    coordinator.skip_next_run("detector not connected in simulated plan")
    assert coordinator.next_index == 1
    assert coordinator.active_run is None
    record = coordinator.campaign.records[0]
    assert record["outcome"] == "skipped"
    assert record["gui_start_to_completion_seconds"] is None
    assert record["stage_seconds"] == {}
    assert record["simulation_only"] is True
    with pytest.raises(ValueError, match="requires a reason"):
        coordinator.skip_next_run(" ")


def test_invalid_fake_timings_do_not_persist_or_release_active_run(tmp_path):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()
    with pytest.raises(ValueError, match="finite and nonnegative"):
        coordinator.complete_run(run.run_id, simulated_gui_duration_seconds=float("nan"))
    with pytest.raises(ValueError, match="stage timings"):
        coordinator.complete_run(run.run_id, stage_seconds={"bad": -1})
    assert coordinator.state is SimulationState.IN_RUN
    assert coordinator.completed_count == 0
    coordinator.complete_run(run.run_id)
    assert coordinator.completed_count == 1


def test_simulated_checkpoint_cannot_be_skipped_by_pause_resume_or_start(tmp_path):
    coordinator = SimulatedCalibrationCoordinator(tmp_path)
    coordinator.start()
    with pytest.raises(RuntimeError, match="speed_checkpoint"):
        coordinator.begin_next_run()
    with pytest.raises(RuntimeError, match="speed_checkpoint"):
        coordinator.pause()
    with pytest.raises(RuntimeError, match="speed_checkpoint"):
        coordinator.resume()
    with pytest.raises(RuntimeError, match="speed_checkpoint"):
        coordinator.start()
    assert coordinator.completed_count == 0


def test_duplicate_plan_or_existing_campaign_files_rejected(tmp_path):
    coordinator = SimulatedCalibrationCoordinator(tmp_path)
    plan = coordinator.runs
    with pytest.raises(ValueError, match="unique"):
        SimulatedCalibrationCoordinator(tmp_path, (plan[0], plan[0]))
    coordinator.start()
    coordinator.acknowledge_simulated_speed(10)
    run = coordinator.begin_next_run()
    coordinator.complete_run(run.run_id)
    conflicting = SimulatedCalibrationCoordinator(tmp_path, plan)
    with pytest.raises(FileExistsError, match="already exists"):
        conflicting.start()
    assert conflicting.state is SimulationState.READY


def test_persistence_failure_halts_and_preserves_prior_record(tmp_path, monkeypatch):
    coordinator = _running(tmp_path)
    run = coordinator.begin_next_run()

    def fail_write(*_args, **_kwargs):
        raise OSError("fake disk full")

    monkeypatch.setattr("logic.ct400_calibration_simulation.append_calibration_record", fail_write)
    coordinator.complete_run(run.run_id)
    assert coordinator.state is SimulationState.FAILED
    assert "fake disk full" in coordinator.failure_reason
    assert coordinator.next_index == 0
    assert coordinator.active_run is None
    with pytest.raises(RuntimeError, match="failed"):
        coordinator.begin_next_run()


def test_interrupted_simulation_journal_restores_without_automatic_resume(tmp_path):
    coordinator = _running(tmp_path)
    first = coordinator.begin_next_run()
    coordinator.complete_run(first.run_id)
    second = coordinator.begin_next_run()
    assert second.run_id != first.run_id
    # An application crash here would leave only the first terminal row.
    restored = CalibrationCampaign(
        coordinator.campaign.campaign_id, coordinator.campaign.created_at_utc, {"simulation_only": True}
    )
    assert restore_campaign_journal(tmp_path, restored) == 1
    assert restored.records[0]["run_id"] == first.run_id
    assert restored.records[0]["simulation_only"] is True
    assert second.run_id not in {record["run_id"] for record in restored.records}
    assert not hasattr(restored, "resume")
