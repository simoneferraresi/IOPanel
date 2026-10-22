"""Qt fake-feature measurement regressions, never a physical Vimba test."""

import pytest
import shiboken6

import tools.characterize_camera_control_latency as diagnostic
from tools.characterize_camera_control_latency import run_scenario


@pytest.mark.parametrize(
    ("mode", "expected_writes"),
    [
        ("direct", 8),
        ("coalesced", 1),
        ("typed", 1),
        ("dual", 2),
        ("mutex_contention", 1),
    ],
)
def test_camera_fake_feature_delay_and_heartbeat(qapp, mode, expected_writes):
    result = run_scenario(
        mode,
        updates=8,
        setter_ms=1,
        repetitions=2,
        contention_ms=2,
    )
    assert result["writes_per_run"] == expected_writes
    assert result["total_completed_writes"] == 2 * expected_writes
    assert result["qt_callback_delay_median_ms"] >= 0
    assert result["qt_callback_delay_p95_ms"] >= 0
    assert result["latest_value_applied"]
    assert not result["native_sdk_used"]


def test_characterizer_destroys_native_panels_before_return(qapp, monkeypatch):
    panels = []
    original = diagnostic.make_panel

    def track(*args):
        result = original(*args)
        panels.append(result[2])
        return result

    monkeypatch.setattr(diagnostic, "make_panel", track)
    run_scenario("dual", updates=4, repetitions=2, setter_ms=0)
    assert len(panels) == 4
    assert all(not shiboken6.isValid(panel) for panel in panels)


def test_camera_characterizer_rejects_invalid_scenarios():
    with pytest.raises(ValueError, match="Unknown"):
        run_scenario("does_not_exist")
    with pytest.raises(ValueError, match="Invalid"):
        run_scenario("coalesced", updates=0)
