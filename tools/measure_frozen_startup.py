"""Measure frozen startup to resource readiness and clean simulation shutdown."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

READY_MARKER = "SMOKE_RESOURCES_OK"
EXIT_MARKER = "Qt aboutToQuit received"


def measure(executable: Path, runs: int, root: Path) -> dict[str, object]:
    executable = executable.resolve()
    results = []
    for run_number in range(1, runs + 1):
        with tempfile.TemporaryDirectory(prefix=f"iopanel-startup-{run_number}-", dir=root) as temporary:
            temp_root = Path(temporary)
            cwd = temp_root / "unrelated-working-directory"
            cwd.mkdir()
            local_app_data = temp_root / "local-app-data"
            log_path = temp_root / "logs" / "smoke.log"
            env = os.environ.copy()
            env["LOCALAPPDATA"] = str(local_app_data)
            env["QT_QPA_PLATFORM"] = "offscreen"
            process = subprocess.Popen(
                [str(executable), "--log-file", str(log_path), "--smoke-test"],
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            started = time.perf_counter()
            ready_seconds = None
            try:
                while process.poll() is None:
                    if log_path.is_file():
                        log = log_path.read_text(encoding="utf-8")
                        if ready_seconds is None and READY_MARKER in log:
                            ready_seconds = time.perf_counter() - started
                    time.sleep(0.01)
                wall_seconds = time.perf_counter() - started
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

            if process.returncode != 0:
                raise RuntimeError(f"{executable} run {run_number} exited {process.returncode}")
            if not log_path.is_file():
                raise RuntimeError(f"{executable} run {run_number} did not create its log")
            log = log_path.read_text(encoding="utf-8")
            if READY_MARKER not in log or EXIT_MARKER not in log:
                raise RuntimeError(f"{executable} run {run_number} missed startup or shutdown evidence")
            if ready_seconds is None:
                raise RuntimeError(f"{executable} run {run_number} did not reach resource readiness")
            results.append({"run": run_number, "ready_seconds": ready_seconds, "wall_seconds": wall_seconds})

    return {
        "executable": executable.name,
        "runs": results,
        "median_ready_seconds": statistics.median(item["ready_seconds"] for item in results),
        "median_wall_seconds": statistics.median(item["wall_seconds"] for item in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--runs", default=5, type=int)
    parser.add_argument("--temp-root", default=Path.cwd(), type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least one")
    args.temp_root.mkdir(parents=True, exist_ok=True)

    results = {
        "baseline": measure(args.baseline, args.runs, args.temp_root),
        "candidate": measure(args.candidate, args.runs, args.temp_root),
    }
    encoded = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
