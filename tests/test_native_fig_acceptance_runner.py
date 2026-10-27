from __future__ import annotations

import json
from typing import Any

from tools.matlab_lifecycle import native_fig_acceptance as acceptance


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_fig_reopen_script_uses_valid_matlab_options_and_keeps_content_assertions() -> None:
    script = acceptance.MATLAB_FIG_REOPEN_VERIFICATION

    assert "openfig(iopanel_native_fig_path, 'new', 'invisible')" in script
    for assertion in (
        "numel(iopanel_native_lines) == 2",
        "get(iopanel_native_det1, 'XData')",
        "get(iopanel_native_det2, 'XData')",
        "get(iopanel_native_det1, 'YData')",
        "get(iopanel_native_det2, 'YData')",
        "get(iopanel_native_det1, 'Color')",
        "get(iopanel_native_det2, 'Color')",
        "'Wavelength (nm)'",
        "'Power (mW)'",
        "'Native acceptance'",
    ):
        assert assertion in script


def test_owned_matlab_exit_observation_waits_for_the_same_process_to_disappear() -> None:
    clock = FakeClock()
    process = {"pid": 1220, "created": "2026-10-27T13:15:10Z"}
    snapshots = iter(([],))

    final_rows, waited_seconds = acceptance.observe_owned_matlab_exit(
        {1220: process},
        [process],
        timeout_seconds=1.0,
        snapshot_fn=lambda: next(snapshots),
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert final_rows == []
    assert waited_seconds == acceptance.POST_CHILD_EXIT_POLL_INTERVAL_SECONDS


def test_owned_matlab_exit_observation_fails_closed_at_its_bound() -> None:
    clock = FakeClock()
    process = {"pid": 1220, "created": "2026-10-27T13:15:10Z"}
    snapshots: list[dict[str, Any]] = []

    final_rows, waited_seconds = acceptance.observe_owned_matlab_exit(
        {1220: process},
        [process],
        timeout_seconds=0.5,
        snapshot_fn=lambda: snapshots.append(process) or [process],
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert final_rows == [process]
    assert waited_seconds == 0.5
    assert len(snapshots) == 2


def test_parent_supervisor_observes_owned_matlab_after_child_exit(tmp_path, monkeypatch) -> None:
    process = {
        "pid": 1220,
        "ppid": 17,
        "name": "MATLAB.exe",
        "path": r"C:\Program Files\MATLAB\R2024b\bin\win64\MATLAB.exe",
        "created": "2026-10-27T13:15:10Z",
    }
    snapshots = iter(([], [process], [process], []))
    monkeypatch.setattr(acceptance, "process_snapshot", lambda: next(snapshots))

    class FinishedChild:
        pid = 17
        returncode = 0
        poll_count = 0

        def poll(self) -> int | None:
            self.poll_count += 1
            return None if self.poll_count == 1 else 0

    monkeypatch.setattr(acceptance.subprocess, "Popen", lambda *args, **kwargs: FinishedChild())
    monkeypatch.setattr(acceptance, "descendants", lambda _rows, _pid: {1220})

    result = acceptance.run_child("normal-fig", tmp_path, deadline_seconds=10)

    report = json.loads((tmp_path / "supervisor.json").read_text(encoding="utf-8"))
    assert result == 0
    assert report["post_child_exit_observation_seconds"] == acceptance.POST_CHILD_EXIT_POLL_INTERVAL_SECONDS
    assert report["test_owned_matlab_processes_observed"][0]["exited_during_post_child_observation"]
