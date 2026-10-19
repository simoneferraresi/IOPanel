"""Contract tests for shared CT400 CSV/MAT serialization and publication."""

from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import loadmat

from logic.power_monitor_export_worker import _write_staged_power_monitor_format
from logic.scan_export_worker import _write_staged_format


def _payload():
    values = np.array(
        [
            [1550.1234567890123, -12.345678901234567],
            [0.0000009, -0.0000009],
            [np.nan, np.inf],
        ],
        dtype=np.float64,
    )
    return SimpleNamespace(
        csv_data=values,
        csv_header='# SchemaVersion: 42\n# Comment: "optical test µ"',
        mat_data={"measurements": values, "comment": "optical test µ"},
    )


@pytest.mark.parametrize(
    ("writer", "temp_prefix"),
    [
        (_write_staged_format, ".iopanel-scan-"),
        (_write_staged_power_monitor_format, ".iopanel-monitor-"),
    ],
)
def test_shared_serializer_preserves_archival_csv_bytes_and_cleanup(tmp_path, writer, temp_prefix):
    payload = _payload()
    destination = tmp_path / "record.csv"
    expected = io.StringIO(newline="")
    np.savetxt(
        expected,
        payload.csv_data,
        delimiter=",",
        header=payload.csv_header,
        comments="",
        fmt="%.17g",
    )

    writer("CSV", destination, payload, overwrite_confirmed=False)

    assert destination.read_bytes() == expected.getvalue().encode("utf-8")
    assert set(tmp_path.iterdir()) == {destination}
    assert not list(tmp_path.glob(temp_prefix + "*"))


def test_scan_csv_round_trips_adjacent_float64_values(tmp_path):
    payload = _payload()
    payload.csv_data = np.array(
        [
            [np.nextafter(1550.0, np.inf), np.nextafter(-12.5, -np.inf)],
            [1550.1234567890123, -0.0000009],
        ],
        dtype=np.float64,
    )
    destination = tmp_path / "scan.csv"

    _write_staged_format("CSV", destination, payload, overwrite_confirmed=False)

    np.testing.assert_array_equal(
        np.loadtxt(destination, delimiter=",", comments="#"),
        payload.csv_data,
    )


def test_power_monitor_csv_round_trips_double_precision_elapsed_times(tmp_path):
    payload = _payload()
    payload.csv_data = np.array(
        [
            [np.nextafter(0.1, 1.0), -12.345678901234567],
            [np.nextafter(0.2, 1.0), 0.0000009],
        ],
        dtype=np.float64,
    )
    destination = tmp_path / "monitor.csv"

    _write_staged_power_monitor_format("CSV", destination, payload, overwrite_confirmed=False)

    np.testing.assert_array_equal(
        np.loadtxt(destination, delimiter=",", comments="#"),
        payload.csv_data,
    )


@pytest.mark.parametrize(
    ("writer", "temp_prefix"),
    [
        (_write_staged_format, ".iopanel-scan-"),
        (_write_staged_power_monitor_format, ".iopanel-monitor-"),
    ],
)
def test_shared_mat_serializer_preserves_payload_and_cleans_stage(tmp_path, writer, temp_prefix):
    payload = _payload()
    destination = tmp_path / "record.mat"

    writer("MAT", destination, payload, overwrite_confirmed=False)

    result = loadmat(destination, simplify_cells=True)
    np.testing.assert_array_equal(result["measurements"], payload.mat_data["measurements"])
    assert result["comment"] == "optical test µ"
    assert set(tmp_path.iterdir()) == {destination}
    assert not list(tmp_path.glob(temp_prefix + "*"))


@pytest.mark.parametrize(
    ("writer", "error_context", "temp_prefix"),
    [
        (_write_staged_format, "scan", ".iopanel-scan-"),
        (_write_staged_power_monitor_format, "Power Monitor", ".iopanel-monitor-"),
    ],
)
def test_unsupported_format_fails_and_leaves_no_files(tmp_path, writer, error_context, temp_prefix):
    with pytest.raises(ValueError, match=f"Unsupported {error_context} export format"):
        writer("PNG", tmp_path / "not-created.png", _payload(), overwrite_confirmed=False)

    assert list(tmp_path.iterdir()) == []
    assert not list(tmp_path.glob(temp_prefix + "*"))
