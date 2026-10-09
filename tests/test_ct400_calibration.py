import csv
import json

import pytest

from logic.ct400_calibration import (
    ISSUE_140_CASES,
    CalibrationCampaign,
    CalibrationCase,
    append_calibration_record,
    build_calibration_matrix,
    restore_campaign_journal,
    summarize_calibration,
)


def test_issue_140_matrix_is_exactly_18_runs_and_grouped_by_connect_speed():
    runs = build_calibration_matrix()
    assert len(runs) == 18
    assert [run.case_id for run in runs] == [case for case in ("A", "B", "C", "E", "F") for _ in range(3)] + ["D"] * 3
    assert [run.speed_block_nm_s for run in runs] == [10] * 15 + [5] * 3
    assert [run.repetition for run in runs] == [1, 2, 3] * 6
    assert len({run.run_id for run in runs}) == len(runs)
    by_case = {case.case_id: case for case in ISSUE_140_CASES}
    for run in runs:
        case = by_case[run.case_id]
        assert run.start_wavelength_nm == case.start_wavelength_nm
        assert run.end_wavelength_nm == case.end_wavelength_nm
        assert run.resolution_pm == case.resolution_pm
        assert run.detectors == case.detectors
        assert run.nominal_sweep_seconds == (case.end_wavelength_nm - case.start_wavelength_nm) / case.speed_nm_s
    assert runs[0].predicted_sample_count == 5001
    assert runs[-1].predicted_sample_count == 35001


def test_matrix_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        CalibrationCase("bad", 1502, 1500, 10, 1)
    with pytest.raises(ValueError):
        build_calibration_matrix((CalibrationCase("a", 1500, 1501, 10, 1),) * 2)
    with pytest.raises(ValueError, match="contiguous"):
        build_calibration_matrix(
            (
                CalibrationCase("a", 1500, 1501, 10, 1),
                CalibrationCase("b", 1500, 1501, 5, 1),
                CalibrationCase("c", 1500, 1501, 10, 1),
            )
        )


def test_records_persist_incrementally_as_csv_and_manifest(tmp_path):
    campaign = CalibrationCampaign.create({"laser_input": 1})
    first = {
        "run_id": "one",
        "outcome": "success",
        "nominal_sweep_seconds": 2.0,
        "gui_start_to_completion_seconds": 3.4,
        "stage_seconds": {"scan": 2.5},
    }
    append_calibration_record(tmp_path, campaign, first)
    append_calibration_record(tmp_path, campaign, {"run_id": "two", "outcome": "failed", "error": "test"})
    csv_path = tmp_path / f"{campaign.campaign_id}.csv"
    json_path = tmp_path / f"{campaign.campaign_id}.json"
    journal_path = tmp_path / f"{campaign.campaign_id}.jsonl"
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["run_id"] for row in rows] == ["one", "two"]
    assert json.loads(rows[0]["stage_seconds"]) == {"scan": 2.5}
    manifest = json.loads(json_path.read_text(encoding="utf-8"))
    assert manifest["records"] == campaign.records
    assert manifest["analysis_summary"]["successful_measurements"] == 1
    assert len(journal_path.read_text(encoding="utf-8").splitlines()) == 2
    with pytest.raises(ValueError, match="already recorded"):
        append_calibration_record(tmp_path, campaign, first)
    collision = CalibrationCampaign(campaign.campaign_id, campaign.created_at_utc, {})
    with pytest.raises(FileExistsError):
        append_calibration_record(tmp_path, collision, {"run_id": "unrelated"})


def test_interrupted_campaign_restores_journal_without_resuming(tmp_path):
    campaign = CalibrationCampaign.create({"simulated": True})
    append_calibration_record(tmp_path, campaign, {"campaign_id": campaign.campaign_id, "run_id": "done"})
    journal = tmp_path / f"{campaign.campaign_id}.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"run_id":"partial"')

    restored = CalibrationCampaign(campaign.campaign_id, campaign.created_at_utc, campaign.settings)
    assert restore_campaign_journal(tmp_path, restored) == 1
    assert [record["run_id"] for record in restored.records] == ["done"]
    assert (tmp_path / f"{campaign.campaign_id}.jsonl").read_bytes().endswith(b"\n")
    assert json.loads((tmp_path / f"{campaign.campaign_id}.json").read_text(encoding="utf-8"))["records"] == [
        {"campaign_id": campaign.campaign_id, "run_id": "done"}
    ]


def test_summary_reports_median_error_and_run_variation():
    summary = summarize_calibration(
        [
            {
                "run_id": "1",
                "case_id": "A",
                "outcome": "success",
                "speed_block_nm_s": 10,
                "nominal_sweep_seconds": 2,
                "gui_start_to_completion_seconds": 4,
                "stage_seconds": {"scan_wait_end_seconds": 3},
            },
            {
                "run_id": "2",
                "case_id": "A",
                "outcome": "success",
                "speed_block_nm_s": 10,
                "nominal_sweep_seconds": 2,
                "gui_start_to_completion_seconds": 6,
                "stage_seconds": {"scan_wait_end_seconds": 5},
            },
            {
                "run_id": "3",
                "outcome": "failed",
                "speed_block_nm_s": 10,
                "nominal_sweep_seconds": 2,
                "gui_start_to_completion_seconds": 8,
            },
        ]
    )
    result = summary["by_speed_nm_s"]["10"]
    assert summary["successful_measurements"] == 2
    assert result["median_error_seconds"] == 3
    assert result["run_to_run_sd_seconds"] == 1
    assert summary["by_case"]["A"]["median_stage_seconds"]["scan_wait_end_seconds"] == 4


def test_summary_tolerates_missing_or_partial_stage_timings():
    summary = summarize_calibration(
        [
            {
                "run_id": "partial",
                "case_id": "A",
                "outcome": "success",
                "speed_block_nm_s": 10,
                "nominal_sweep_seconds": 1,
                "gui_start_to_completion_seconds": 2,
                "stage_seconds": None,
            }
        ]
    )
    assert summary["by_case"]["A"]["median_stage_seconds"] == {}
