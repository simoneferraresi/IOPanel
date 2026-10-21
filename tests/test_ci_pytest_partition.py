"""Contract tests for mandatory, disjoint Windows CI pytest partitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import ci_pytest_partition as partitions
from tools import qt_stall_stress_supervisor as supervisor


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test_camera_view_modes.py", "camera"),
        ("test_mainwindow_camera_integration.py", "camera"),
        ("test_mainwindow_concurrent_simulated_acquisition.py", "camera"),
        ("test_power_monitor_trace_widget.py", "plot"),
        ("test_scan_import_async_gui.py", "plot"),
        ("test_plot_widgets.py", "plot"),
        ("test_scan_export_async.py", "plot"),
        ("test_dummy_ct400.py", "core"),
        ("test_new_module.py", "core"),
    ],
)
def test_group_assignment_is_deterministic(name, expected):
    assert partitions.group_for_module(Path("tests") / name) == expected


def test_module_paths_partition_all_repository_tests_exactly_once():
    modules = partitions.discover_modules()
    groups = {group: partitions.paths_for_group(group) for group in partitions.GROUPS}
    selected = [module for group in partitions.GROUPS for module in groups[group]]
    assert len(modules) == len(set(selected))
    assert set(selected) == set(modules)
    assert all(groups.values())
    assert "tests/test_ci_pytest_partition.py" in groups["core"]


def test_new_test_file_is_assigned_to_core_without_manifest_edits(tmp_path):
    directory = tmp_path / "tests"
    directory.mkdir()
    for name in ("test_camera_example.py", "test_plot_example.py", "test_new_feature.py"):
        (directory / name).write_text("def test_example(): pass\n", encoding="utf-8")
    assert partitions.paths_for_group("camera", tmp_path) == ["tests/test_camera_example.py"]
    assert partitions.paths_for_group("plot", tmp_path) == ["tests/test_plot_example.py"]
    assert partitions.paths_for_group("core", tmp_path) == ["tests/test_new_feature.py"]


def test_collection_parser_preserves_parametrizations_and_whitespace():
    stdout = (
        "tests/test_a.py::test_one[param with spaces]\n"
        "tests\\test_b.py::TestClass::test_two[1]\n"
        "\n2 tests collected in 0.03s\n"
    )
    assert partitions.parse_nodeids(stdout) == [
        "tests/test_a.py::test_one[param with spaces]",
        "tests/test_b.py::TestClass::test_two[1]",
    ]


def test_supervisor_selects_exact_group_modules_without_changing_stress():
    for group in partitions.GROUPS:
        command = supervisor._pytest_command(suite=True, suite_group=group)
        assert command[-len(partitions.paths_for_group(group)) :] == partitions.paths_for_group(group)
        assert "tests" not in command
    assert supervisor._pytest_command(suite=True)[-1] == "tests"
    assert supervisor._pytest_command(suite=False)[-2:] == ["-s", "tests/test_qt_stall_stress.py"]


def test_unknown_or_incompatible_group_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="Unknown pytest group"):
        partitions.paths_for_group("unknown")
    with pytest.raises(ValueError, match="requires --suite"):
        supervisor._pytest_command(suite=False, suite_group="camera")
    with pytest.raises(ValueError, match="requires --suite"):
        supervisor.supervise(tmp_path / "invalid", 30, 1, None, suite_group="camera")
    assert not (tmp_path / "invalid").exists()
