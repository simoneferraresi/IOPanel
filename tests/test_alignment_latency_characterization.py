"""Real alignment worker exercised with synthetic DLL and virtual waits."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from hardware import alignment_worker
from tools.characterize_alignment_latency import (
    VirtualClock,
    characterize,
    characterize_search,
    make_worker,
    mapping_settings,
)


@pytest.mark.parametrize(("shape", "samples"), [((2, 3), 1), ((10, 10), 3), ((3, 2), 5)])
def test_mapping_command_counts_and_data_orientation(shape, samples):
    row = characterize(*shape, samples)
    n = shape[0] * shape[1]
    assert row["dll_calls_observed"] == 6 * n + 18
    assert row["detector_reads_observed"] == n * samples
    assert row["progress_signals"] == n
    assert row["wait_ms_observed"] == 200 + 3 * n + (20 * n * samples if samples > 1 else 0)
    assert row["restored_initial_position"]
    assert row["noop_voltage_writes"] > 0
    assert not row["physical_measurement"]


def test_map_restores_position_and_disables_laser_after_read_error():
    clock = VirtualClock()
    worker, dll, meter = make_worker(clock)
    meter.fail_after = 2
    errors = []
    finished = []
    worker.error_occurred.connect(errors.append)
    worker.operation_finished.connect(lambda: finished.append(True))
    worker.begin_operation()

    with patch.object(alignment_worker, "QThread", SimpleNamespace(msleep=clock.sleep_ms)):
        worker.run_mapping(mapping_settings(2, 3, 1))

    assert errors and "injected synthetic power read failure" in errors[0]
    assert finished == [True]
    assert meter.laser_states == [1, 0]
    assert not worker._is_running
    assert np.isclose(dll.voltage["x"], 5.0)
    assert np.isclose(dll.voltage["y"], 5.0)


def test_mapping_stop_cancels_following_points_and_restores_stage():
    clock = VirtualClock()
    worker, dll, meter = make_worker(clock)
    meter.on_read = lambda: worker.stop() if meter.read_calls == 2 else None
    results = []
    worker.mapping_finished.connect(lambda *values: results.append(values))
    worker.begin_operation()

    with patch.object(alignment_worker, "QThread", SimpleNamespace(msleep=clock.sleep_ms)):
        worker.run_mapping(mapping_settings(3, 4, 1))

    assert not results
    assert meter.read_calls == 2
    assert meter.laser_states == [1, 0]
    assert not worker._is_running
    assert np.isclose(dll.voltage["x"], 5.0)
    assert np.isclose(dll.voltage["y"], 5.0)


@pytest.mark.parametrize("mode", ["fine", "spiral"])
def test_fake_fine_and_spiral_latency_path(mode):
    row = characterize_search(mode)
    assert row["dll_calls_observed"] > 0
    assert row["detector_reads_observed"] > 0
    assert row["virtual_wait_ms_observed"] >= 200
    assert row["laser_cleanup_requested"]
    assert not row["physical_measurement"]
