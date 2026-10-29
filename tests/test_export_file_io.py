"""Contract tests for shared CT400 CSV/MAT serialization and publication."""

from __future__ import annotations

import io
import os
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import loadmat

from logic import export_file_io
from logic.export_file_io import derive_export_targets
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


@pytest.mark.parametrize(
    ("name", "scan_expected", "monitor_expected"),
    [
        ("measurement", "measurement.csv", "measurement.csv"),
        ("measurement.MAT", "measurement.csv", "measurement.csv"),
        ("measurement.fig", "measurement.csv", "measurement.fig.csv"),
        ("measurement.txt", "measurement.txt.csv", "measurement.txt.csv"),
    ],
)
def test_shared_target_derivation_preserves_scan_and_monitor_suffix_policies(name, scan_expected, monitor_expected):
    from logic.power_monitor_export import derive_power_monitor_export_targets
    from ui.plot_widgets import derive_scan_export_targets

    scan = derive_scan_export_targets(name, include_csv=True, include_mat=False, include_fig=False)
    monitor = derive_power_monitor_export_targets(name, include_csv=True, include_mat=False)
    assert scan["CSV"].name == scan_expected
    assert monitor["CSV"].name == monitor_expected
    assert list(scan) == ["CSV"]
    assert list(monitor) == ["CSV"]


def test_shared_target_derivation_preserves_selection_order_and_no_format_case():
    from ui.plot_widgets import derive_scan_export_targets

    actual = derive_scan_export_targets("scan.csv", include_csv=False, include_mat=True, include_fig=True)
    assert {name: path.name for name, path in actual.items()} == {"MAT": "scan.mat", "FIG": "scan.fig"}
    assert list(actual) == ["MAT", "FIG"]
    assert derive_export_targets("scan.csv", formats={}, recognized_suffixes=frozenset({".csv"})) == {}


def _emulate_virtual_windows_drive(monkeypatch, *, before_rename=None):
    """A virtual drive rejects hard links but accepts Windows no-clobber rename."""
    real_os = export_file_io.os
    calls = []

    def rename(stage, destination):
        calls.append("rename")
        if before_rename is not None:
            before_rename(stage, destination)
        if destination.exists():
            raise FileExistsError(f"Destination was created during export: {destination}")
        real_os.rename(stage, destination)

    def replace(stage, destination):
        calls.append("replace")
        real_os.replace(stage, destination)

    def link(stage, destination):
        raise OSError(1, "Incorrect function: hard links unsupported")

    # Replace only this module's os binding, never os.name process-wide.
    monkeypatch.setattr(
        export_file_io,
        "os",
        SimpleNamespace(
            name="nt",
            fdopen=real_os.fdopen,
            fsync=real_os.fsync,
            rename=rename,
            replace=replace,
            link=link,
        ),
    )
    return calls


@pytest.mark.parametrize(
    ("writer", "temp_prefix"),
    [
        (_write_staged_format, ".iopanel-scan-"),
        (_write_staged_power_monitor_format, ".iopanel-monitor-"),
    ],
)
@pytest.mark.parametrize("format_name", ["CSV", "MAT"])
def test_virtual_windows_drive_exports_without_hard_links(tmp_path, monkeypatch, writer, temp_prefix, format_name):
    calls = _emulate_virtual_windows_drive(monkeypatch)
    destination = tmp_path / f"measurement.{format_name.lower()}"
    payload = _payload()

    writer(format_name, destination, payload, overwrite_confirmed=False)

    assert calls == ["rename"]
    if format_name == "CSV":
        np.testing.assert_array_equal(
            np.loadtxt(destination, delimiter=",", comments="#"), payload.csv_data
        )
    else:
        result = loadmat(destination, simplify_cells=True)
        np.testing.assert_array_equal(result["measurements"], payload.mat_data["measurements"])
    assert list(tmp_path.iterdir()) == [destination]
    assert not list(tmp_path.glob(temp_prefix + "*"))


@pytest.mark.parametrize("writer", [_write_staged_format, _write_staged_power_monitor_format])
@pytest.mark.parametrize("format_name", ["CSV", "MAT"])
def test_virtual_windows_drive_race_never_overwrites(tmp_path, monkeypatch, writer, format_name):
    def concurrent_creator(_stage, destination):
        destination.write_bytes(b"created by another process")

    calls = _emulate_virtual_windows_drive(monkeypatch, before_rename=concurrent_creator)
    destination = tmp_path / f"measurement.{format_name.lower()}"

    with pytest.raises(FileExistsError):
        writer(format_name, destination, _payload(), overwrite_confirmed=False)

    assert calls == ["rename"]
    assert destination.read_bytes() == b"created by another process"
    assert set(tmp_path.iterdir()) == {destination}


@pytest.mark.parametrize("writer", [_write_staged_format, _write_staged_power_monitor_format])
@pytest.mark.parametrize("format_name", ["CSV", "MAT"])
def test_virtual_windows_drive_confirmed_overwrites_still_use_replace(
    tmp_path, monkeypatch, writer, format_name
):
    calls = _emulate_virtual_windows_drive(monkeypatch)
    destination = tmp_path / f"measurement.{format_name.lower()}"
    destination.write_bytes(b"previous measurement")

    writer(format_name, destination, _payload(), overwrite_confirmed=True)

    assert calls == ["replace"]
    assert destination.read_bytes() != b"previous measurement"
    assert set(tmp_path.iterdir()) == {destination}


@pytest.mark.parametrize("writer", [_write_staged_format, _write_staged_power_monitor_format])
def test_virtual_windows_rename_failure_is_reported_without_partial_target(tmp_path, monkeypatch, writer):
    def refuse_rename(_stage, _destination):
        raise OSError(1, "Incorrect function: rename unsupported")

    calls = _emulate_virtual_windows_drive(monkeypatch, before_rename=refuse_rename)
    destination = tmp_path / "measurement.csv"

    with pytest.raises(OSError, match="rename unsupported"):
        writer("CSV", destination, _payload(), overwrite_confirmed=False)

    assert calls == ["rename"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows no-overwrite rename semantics")
def test_native_windows_rename_preserves_unconfirmed_destination(tmp_path):
    stage = tmp_path / "stage.tmp"
    destination = tmp_path / "measurement.csv"
    stage.write_bytes(b"new measurement")
    destination.write_bytes(b"existing measurement")

    with pytest.raises(FileExistsError):
        export_file_io._publish_staged_file(stage, destination, overwrite_confirmed=False)

    assert destination.read_bytes() == b"existing measurement"
    assert stage.read_bytes() == b"new measurement"
