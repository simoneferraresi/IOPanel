"""Repeat mandatory native Windows Qt partitions without retrying failures.

Each entry invokes the existing bounded supervisor in a fresh process. A green
pytest summary followed by a native exit remains a failed entry. Evidence
directories are exclusive, and the campaign stops at its first failed entry.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def run_campaign(
    repository: Path,
    output: Path,
    python: Path,
    counts: dict[str, int],
    stress_cycles: int,
) -> int:
    if any(value < 0 for value in counts.values()) or not 0 <= stress_cycles <= 1000:
        raise ValueError("Run counts must be nonnegative; stress cycles must be at most 1000")
    repository, output, python = repository.resolve(), output.resolve(), python.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    env.pop("IOPANEL_QT_STRESS_CONFIG", None)
    env.pop("IOPANEL_QT_STRESS_CYCLES", None)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    metadata = subprocess.check_output(
        [
            str(python),
            "-c",
            (
                "import sys,PySide6; from PySide6.QtCore import qVersion; "
                "print(sys.version); print('PySide6='+PySide6.__version__); print('Qt='+qVersion())"
            ),
        ],
        cwd=repository,
        env=env,
        text=True,
        timeout=30,
    ).strip()
    report: dict = {
        "source_commit": revision,
        "source_changes": subprocess.check_output(["git", "status", "--porcelain"], cwd=repository, text=True),
        "environment": metadata,
        "python": str(python),
        "platform": sys.platform,
        "started_utc": datetime.now(UTC).isoformat(),
        "requested": counts,
        "stress_cycles": stress_cycles,
        "runs": [],
        "complete": False,
    }

    def save() -> None:
        (output / "campaign.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    with (output / "collection.log").open("w", encoding="utf-8") as log:
        collection = subprocess.run(
            [str(python), "-m", "tools.ci_pytest_partition", "--verify"],
            cwd=repository,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=500,
            check=False,
        )
    report["collection_returncode"] = collection.returncode
    save()
    if collection.returncode:
        return 1

    entries = [
        (group, n + 1, 1)
        for n in range(max(counts.values(), default=0))
        for group, count in counts.items()
        if n < count
    ]
    if stress_cycles:
        entries.insert(0, ("stress", 1, stress_cycles))
    for group, number, cycles in entries:
        target = output / f"{group}-{number:02d}"
        command = [
            str(python),
            "-m",
            "tools.qt_stall_stress_supervisor",
            "--cycles",
            str(cycles),
            "--timeout-seconds",
            "300",
            "--output",
            str(target),
        ]
        if group != "stress":
            command.extend(["--suite", "--suite-group", group])
        print(f"Starting {group} {number}: {revision}", flush=True)
        with (output / f"{group}-{number:02d}.supervisor.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command,
                cwd=repository,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=450,
                check=False,
            )
        manifest = target / "manifest.json"
        details = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
        passed = result.returncode == 0 and details.get("returncode") == 0 and not details.get("timed_out")
        passed = passed and "supervisor_error" not in details
        row = {
            "group": group,
            "number": number,
            "command": command,
            "supervisor_returncode": result.returncode,
            "child_returncode": details.get("returncode"),
            "timed_out": details.get("timed_out"),
            "last_progress": details.get("last_progress"),
            "passed": passed,
        }
        report["runs"].append(row)
        save()
        print(json.dumps(row), flush=True)
        if not passed:
            return 1
    report["finished_utc"] = datetime.now(UTC).isoformat()
    report["complete"] = True
    save()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core-runs", type=int, default=3)
    parser.add_argument("--camera-runs", type=int, default=30)
    parser.add_argument("--plot-runs", type=int, default=20)
    parser.add_argument("--stress-cycles", type=int, default=100)
    args = parser.parse_args()
    return run_campaign(
        args.repository,
        args.output,
        args.python,
        {"core": args.core_runs, "camera": args.camera_runs, "plot": args.plot_runs},
        args.stress_cycles,
    )


if __name__ == "__main__":
    raise SystemExit(main())
