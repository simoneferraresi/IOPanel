"""Explicit, hardware-inaccessible CT400 ETA calibration *simulation* coordinator.

This is an offline state-machine prototype for Issue #140. It does not import
CT400, ScanWorker, Qt, or any instrument interface, and cannot dispatch a scan.
Each fake run is begun and resolved explicitly by the caller using synthetic
data. Physical UI Start remains disabled pending a separate safety review.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from logic.ct400_calibration import (
    CalibrationCampaign,
    CalibrationRun,
    append_calibration_record,
    build_calibration_matrix,
)

SimulatedOutcome = Literal["success", "warning", "failed", "cancelled"]


class SimulationState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    IN_RUN = "in_run"
    PAUSED = "paused"
    SPEED_CHECKPOINT = "speed_checkpoint"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ABORTING = "aborting"
    ABORTED = "aborted"
    FAILED = "failed"
    COMPLETED = "completed"


class SimulatedCalibrationCoordinator:
    """A single-owner, synchronous state machine with no hardware entry points.

    Calling begin_next_run only *returns* an immutable plan entry. The caller
    must explicitly provide fake results to complete_run. Stop/abort during an
    active fake run prevents any later run, but never invokes native ScanStop.
    """

    def __init__(self, directory: Path, runs: tuple[CalibrationRun, ...] | None = None):
        plan = build_calibration_matrix() if runs is None else tuple(runs)
        if not plan:
            raise ValueError("A simulated calibration plan cannot be empty")
        campaign_id = plan[0].campaign_id
        if any(run.campaign_id != campaign_id for run in plan):
            raise ValueError("All planned runs must belong to the same campaign")
        if len({run.run_id for run in plan}) != len(plan):
            raise ValueError("Planned run IDs must be unique")
        self.directory = Path(directory)
        self.runs = plan
        self.campaign = CalibrationCampaign(
            campaign_id=campaign_id,
            created_at_utc=datetime.now(UTC).isoformat(),
            settings={"simulation_only": True, "planned_run_count": len(plan)},
        )
        self.state = SimulationState.READY
        self.next_index = 0
        self.active_run: CalibrationRun | None = None
        self.failure_reason: str | None = None
        self._approved_simulated_speed: float | None = None
        self._pause_after_run = False
        self._abort_reason: str | None = None

    @property
    def completed_count(self) -> int:
        return len(self.campaign.records)

    @property
    def pending_speed_nm_s(self) -> float | None:
        if self.state is SimulationState.SPEED_CHECKPOINT and self.next_index < len(self.runs):
            return self.runs[self.next_index].speed_block_nm_s
        return None

    def start(self) -> None:
        self._require(SimulationState.READY)
        # Do not join or overwrite artifacts belonging to a previous campaign.
        if any(
            (self.directory / f"{self.campaign.campaign_id}.{suffix}").exists() for suffix in ("jsonl", "json", "csv")
        ):
            raise FileExistsError("A campaign with this ID already exists")
        self._ready_for_next()

    def acknowledge_simulated_speed(self, speed_nm_s: float) -> None:
        """Clear a *simulation* checkpoint; never Connect or reconfigure a laser."""
        self._require(SimulationState.SPEED_CHECKPOINT)
        required_speed = self.runs[self.next_index].speed_block_nm_s
        if not math.isfinite(speed_nm_s) or speed_nm_s != required_speed:
            raise ValueError(f"Simulated checkpoint requires {required_speed:g} nm/s")
        self._approved_simulated_speed = required_speed
        self.state = SimulationState.RUNNING

    def begin_next_run(self) -> CalibrationRun:
        """Reserve exactly one fake run, without starting an instrument."""
        self._require(SimulationState.RUNNING)
        if self.active_run is not None or self.next_index >= len(self.runs):
            raise RuntimeError("No unowned simulated run is available")
        run = self.runs[self.next_index]
        if run.speed_block_nm_s != self._approved_simulated_speed:
            self.state = SimulationState.SPEED_CHECKPOINT
            raise RuntimeError("A simulated speed checkpoint is required")
        self.active_run = run
        self.state = SimulationState.IN_RUN
        return run

    def complete_run(
        self,
        run_id: str,
        *,
        outcome: SimulatedOutcome = "success",
        simulated_gui_duration_seconds: float | None = None,
        stage_seconds: dict[str, float] | None = None,
        reason: str = "",
    ) -> None:
        """Persist a synthetic terminal result; reject stale or repeated IDs."""
        self._require(SimulationState.IN_RUN, SimulationState.STOPPING, SimulationState.ABORTING)
        run = self.active_run
        if run is None or run.run_id != run_id:
            raise ValueError("Stale or mismatched simulated run ID")
        if outcome not in ("success", "warning", "failed", "cancelled"):
            raise ValueError("Unsupported simulated outcome")
        duration = (
            run.nominal_sweep_seconds + 0.25
            if simulated_gui_duration_seconds is None
            else simulated_gui_duration_seconds
        )
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise ValueError("Synthetic GUI duration must be finite and nonnegative")
        stages = {} if stage_seconds is None else dict(stage_seconds)
        if any(
            not isinstance(name, str) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
            for name, value in stages.items()
        ):
            raise ValueError("Synthetic stage timings must be finite, nonnegative numbers")
        self._persist_run(
            run,
            outcome,
            float(duration),
            stages,
            reason or self._abort_reason or "",
        )
        if self.state is SimulationState.FAILED:  # Persistence failure, fail closed.
            return
        if self.state is SimulationState.STOPPING or outcome == "cancelled":
            self.state = SimulationState.STOPPED
        elif self.state is SimulationState.ABORTING:
            self.state = SimulationState.ABORTED
        elif outcome == "failed":
            self.state = SimulationState.FAILED
            self.failure_reason = reason or "Synthetic run failed"
        elif self._pause_after_run or outcome == "warning":
            self._pause_after_run = False
            self.state = SimulationState.PAUSED
        else:
            self._ready_for_next()

    def skip_next_run(self, reason: str) -> None:
        """Journal a skipped fake run, e.g. an unwired detector case."""
        self._require(SimulationState.RUNNING)
        if not reason.strip():
            raise ValueError("A simulated skipped run requires a reason")
        run = self.runs[self.next_index]
        if run.speed_block_nm_s != self._approved_simulated_speed:
            self.state = SimulationState.SPEED_CHECKPOINT
            raise RuntimeError("A simulated speed checkpoint is required")
        self._persist_run(run, "skipped", None, {}, reason)
        if self.state is not SimulationState.FAILED:
            self._ready_for_next()

    def pause(self) -> None:
        if self.state is SimulationState.RUNNING:
            self.state = SimulationState.PAUSED
        elif self.state is SimulationState.IN_RUN:
            self._pause_after_run = True
        else:
            raise RuntimeError(f"Cannot pause in state {self.state.value}")

    def resume(self) -> None:
        self._require(SimulationState.PAUSED)
        self._pause_after_run = False
        self._ready_for_next()

    def stop(self) -> None:
        """Stop scheduling; active fake results may still be finalized."""
        if self.state is SimulationState.IN_RUN:
            self.state = SimulationState.STOPPING
        elif self.state in (SimulationState.RUNNING, SimulationState.PAUSED, SimulationState.SPEED_CHECKPOINT):
            self.state = SimulationState.STOPPED
        elif self.state not in (SimulationState.STOPPING, SimulationState.STOPPED):
            raise RuntimeError(f"Cannot stop in state {self.state.value}")

    def abort(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Aborting a simulated campaign requires a reason")
        if self.state in (SimulationState.IN_RUN, SimulationState.STOPPING):
            self._abort_reason = reason
            self.state = SimulationState.ABORTING
        elif self.state in (
            SimulationState.READY,
            SimulationState.RUNNING,
            SimulationState.PAUSED,
            SimulationState.SPEED_CHECKPOINT,
        ):
            self.failure_reason = reason
            self.state = SimulationState.ABORTED
        else:
            raise RuntimeError(f"Cannot abort in state {self.state.value}")

    def _persist_run(
        self,
        run: CalibrationRun,
        outcome: str,
        duration: float | None,
        stages: dict[str, float],
        reason: str,
    ) -> None:
        record = {
            **asdict(run),
            "simulation_only": True,
            "outcome": outcome,
            "gui_start_to_completion_seconds": duration,
            "stage_seconds": stages,
            "reason": reason,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            append_calibration_record(self.directory, self.campaign, record)
        except Exception as exc:  # noqa: BLE001  # Fail closed on any persistence or serialization error.
            # A JSONL row may already be durable if the CSV/manifest update
            # failed. Do not silently retry or schedule another fake run.
            self.state = SimulationState.FAILED
            self.failure_reason = f"Simulated record persistence failed: {type(exc).__name__}: {exc}"
            self.active_run = None
            return
        self.next_index += 1
        self.active_run = None

    def _ready_for_next(self) -> None:
        if self.next_index >= len(self.runs):
            self.state = SimulationState.COMPLETED
        elif self.runs[self.next_index].speed_block_nm_s != self._approved_simulated_speed:
            self.state = SimulationState.SPEED_CHECKPOINT
        else:
            self.state = SimulationState.RUNNING

    def _require(self, *states: SimulationState) -> None:
        if self.state not in states:
            raise RuntimeError(f"Operation unavailable in simulated state {self.state.value}")
