"""Disjoint Windows CI pytest partitions with an exact collected-test coverage gate.

Partition by *module* so all parametrizations and fixtures within a file stay
together. A newly added test module is always assigned to core by default.
The verification step collects the original suite and each partition in
fresh processes, then compares the exact pytest node IDs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("core", "camera", "plot")

# Keep full MainWindow camera lifecycle, recovery, and concurrent acquisition
# tests together in a dedicated Python/Qt process.
CAMERA_PREFIXES = ("test_camera", "test_mainwindow_camera", "test_qt_camera")
CAMERA_FILES = frozenset({"test_mainwindow_concurrent_simulated_acquisition.py"})

# Isolate native pyqtgraph creation and scan/monitor plotting regressions.
PLOT_PREFIXES = ("test_plot", "test_power_monitor", "test_scan_overlay", "test_scan_import_async_gui")
PLOT_FILES = frozenset({"test_scan_export_async.py"})


def group_for_module(path: Path) -> str:
    name = path.name
    if name in CAMERA_FILES or name.startswith(CAMERA_PREFIXES):
        return "camera"
    if name in PLOT_FILES or name.startswith(PLOT_PREFIXES):
        return "plot"
    return "core"


def discover_modules(root: Path = REPOSITORY_ROOT) -> list[str]:
    """Use pytest's default test-module filename patterns under tests/."""
    directory = root / "tests"
    modules = set(directory.rglob("test_*.py")) | set(directory.rglob("*_test.py"))
    if not modules:
        raise ValueError(f"No test modules found under {directory}")
    return sorted(path.relative_to(root).as_posix() for path in modules if path.is_file())


def paths_for_group(group: str, root: Path = REPOSITORY_ROOT) -> list[str]:
    if group not in GROUPS:
        raise ValueError(f"Unknown pytest group: {group!r}")
    paths = [path for path in discover_modules(root) if group_for_module(Path(path)) == group]
    if not paths:
        raise ValueError(f"No modules assigned to required pytest group {group!r}")
    return paths


def parse_nodeids(output: str) -> list[str]:
    """Extract individual pytest node IDs from --collect-only -q output."""
    nodes = []
    for raw in output.splitlines():
        line = raw.strip().replace("\\", "/")
        if line.startswith("tests/") and "::" in line and " " not in line:
            nodes.append(line)
    return nodes


def collect_nodeids(paths: list[str]) -> list[str]:
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "--disable-warnings", *paths]
    env = os.environ.copy()
    env.pop("IOPANEL_QT_STRESS_CONFIG", None)
    env.pop("IOPANEL_QT_STRESS_CYCLES", None)
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"pytest collection failed ({result.returncode}) for {paths[:3]!r}:\n"
            f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )
    nodes = parse_nodeids(result.stdout)
    if not nodes:
        raise RuntimeError(f"No pytest node IDs found in collection output:\n{result.stdout[-2000:]}")
    if len(nodes) != len(set(nodes)):
        raise AssertionError(f"Duplicate pytest node IDs in collection: {paths[:3]!r}")
    return nodes


def verify_partition_coverage() -> dict[str, int]:
    modules = discover_modules()
    grouped = {group: paths_for_group(group) for group in GROUPS}
    assigned = [name for group in GROUPS for name in grouped[group]]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(modules):
        raise AssertionError("CI module partitions contain duplicates or do not cover the tests directory")

    original = set(collect_nodeids(["tests"]))
    collected = {group: set(collect_nodeids(grouped[group])) for group in GROUPS}
    combined = set().union(*collected.values())
    duplicated = set()
    for i, group in enumerate(GROUPS):
        for other in GROUPS[i + 1 :]:
            duplicated.update(collected[group] & collected[other])
    missing = original - combined
    unexpected = combined - original
    if duplicated or missing or unexpected:
        raise AssertionError(
            f"CI pytest partitions differ from original collection: "
            f"duplicates={sorted(duplicated)[:10]}, "
            f"missing={sorted(missing)[:10]}, "
            f"unexpected={sorted(unexpected)[:10]}"
        )
    counts = {group: len(collected[group]) for group in GROUPS}
    print(f"Exact pytest collection preserved: {len(original)} items; per process: {counts}", flush=True)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Compare exact pytest node IDs with full collection")
    args = parser.parse_args()
    if not args.verify:
        parser.error("Use --verify to check that every test is covered once")
    verify_partition_coverage()


if __name__ == "__main__":
    main()
