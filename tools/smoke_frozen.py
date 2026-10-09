"""Run the packaged GUI in a driver-free profile and verify clean shutdown."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_LOG_MARKERS = (
    "SMOKE_RESOURCES_OK",
    "SMOKE_SIMULATION_READY cameras=1/1",
    "Received device: <class 'hardware.dummy_ct400.DummyCT400'>",
    "Camera 'Simulated Camera' is online.",
    "Shutdown complete.",
    "Qt aboutToQuit received",
)
FORBIDDEN_LOG_MARKERS = ("Searching for DLL", "Initializing hardware", "MATLAB Starting")


def run_smoke(executable: Path, timeout_seconds: float) -> None:
    with tempfile.TemporaryDirectory(prefix="iopanel-smoke-") as temp_name:
        root = Path(temp_name)
        cwd = root / "different-working-directory"
        cwd.mkdir()
        local_app_data = root / "local-app-data"
        log_path = root / "logs" / "smoke.log"
        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(local_app_data)
        env["QT_QPA_PLATFORM"] = "offscreen"
        command = [str(executable.resolve()), "--log-file", str(log_path), "--smoke-test"]
        try:
            result = subprocess.run(
                command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_seconds, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Frozen application exceeded {timeout_seconds}s smoke timeout") from exc
        combined = result.stdout + "\n" + result.stderr
        if result.returncode:
            raise RuntimeError(f"Frozen application exited {result.returncode}:\n{combined}")
        if not log_path.is_file():
            raise RuntimeError(f"Expected per-user log was not created: {log_path}\n{combined}")
        log = log_path.read_text(encoding="utf-8")
        missing = [marker for marker in REQUIRED_LOG_MARKERS if marker not in log]
        forbidden = [marker for marker in FORBIDDEN_LOG_MARKERS if marker in log]
        if missing or forbidden:
            raise RuntimeError(f"Smoke evidence mismatch; missing={missing}, forbidden={forbidden}\n{log}\n{combined}")
        physical_config = root / "physical.ini"
        physical_config.write_text("[Instruments]\nct400_backend = physical\n", encoding="utf-8")
        rejected = subprocess.run(
            [str(executable.resolve()), "--config", str(physical_config), "--smoke-test"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        rejected_output = rejected.stdout + "\n" + rejected.stderr
        if rejected.returncode != 2 or "refusing hardware startup" not in rejected_output:
            raise RuntimeError(
                f"Physical smoke profile was not safely rejected (exit={rejected.returncode}):\n{rejected_output}"
            )
        if "Initializing MainWindow" in rejected_output:
            raise RuntimeError("Physical smoke guard ran after MainWindow construction.")
        print("PACKAGED_SMOKE_OK")
        print("Verified simulated CT400 and camera startup, bundled resources, and graceful Qt shutdown.")
        print("The executable ran from a different working directory with per-user logging.")
        print("Verified the packaged executable rejects a physical profile before GUI or hardware startup.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    try:
        run_smoke(args.executable, args.timeout)
    except (OSError, RuntimeError) as exc:
        print(f"PACKAGED_SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
