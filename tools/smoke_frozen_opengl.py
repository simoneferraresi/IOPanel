"""Safely exercise the packaged 3D alignment viewer without MainWindow startup."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUCCESS_MARKER = "SMOKE_OPENGL_VIEWER_OK"
UNAVAILABLE_MARKER = "SMOKE_OPENGL_UNAVAILABLE"
FAILURE_MARKER = "SMOKE_OPENGL_FAILED"


def run_smoke(executable: Path, timeout_seconds: float, qt_platform: str, evidence_dir: Path | None) -> None:
    with tempfile.TemporaryDirectory(prefix="iopanel-opengl-smoke-") as temp_name:
        root = Path(temp_name)
        cwd = root / "different-working-directory"
        cwd.mkdir()
        local_app_data = root / "local-app-data"
        log_path = root / "logs" / "opengl-smoke.log"
        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(local_app_data)
        env["QT_QPA_PLATFORM"] = qt_platform
        command = [str(executable.resolve()), "--log-file", str(log_path), "--smoke-opengl-test"]
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Frozen OpenGL smoke exceeded {timeout_seconds}s") from exc

        combined = result.stdout + "\n" + result.stderr
        if not log_path.is_file():
            raise RuntimeError(f"Packaged OpenGL smoke did not create its log:\n{combined}")
        log = log_path.read_text(encoding="utf-8")
        if evidence_dir is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_path, evidence_dir / "opengl-smoke.log")
        if result.returncode:
            if evidence_dir is not None:
                (evidence_dir / "opengl-smoke.json").write_text(
                    json.dumps(
                        {
                            "result": "failed",
                            "qt_platform": qt_platform,
                            "exit_code": result.returncode,
                            "executable": executable.name,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            raise RuntimeError(f"Packaged OpenGL smoke exited {result.returncode}:\n{log}\n{combined}")
        if FAILURE_MARKER in log or "Error while drawing item" in combined or "GLError" in combined:
            raise RuntimeError(f"Packaged OpenGL viewer reported a rendering failure:\n{log}\n{combined}")
        physical_config = root / "physical.ini"
        physical_config.write_text("[Instruments]\nct400_backend = physical\n", encoding="utf-8")
        rejected = subprocess.run(
            [str(executable.resolve()), "--config", str(physical_config), "--smoke-opengl-test"],
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
                f"OpenGL smoke did not fail closed on a physical profile "
                f"(exit={rejected.returncode}):\n{rejected_output}"
            )
        if "Initializing MainWindow" in log or "Initializing MainWindow" in rejected_output:
            raise RuntimeError("Hardware-independent OpenGL smoke constructed MainWindow")

        if SUCCESS_MARKER in log:
            image = root / "logs" / "smoke-opengl.png"
            framebuffer = root / "logs" / "smoke-opengl-framebuffer.png"
            colorbar = root / "logs" / "smoke-opengl-colorbar.png"
            if (
                not image.is_file()
                or image.stat().st_size == 0
                or not framebuffer.is_file()
                or framebuffer.stat().st_size == 0
                or not colorbar.is_file()
                or colorbar.stat().st_size == 0
                or "screenshot" not in log
                or "framebuffer" not in log
                or "colorbar" not in log
            ):
                raise RuntimeError(f"OpenGL success marker omitted renderer or framebuffer evidence:\n{log}")
            outcome = "rendered"
            print("PACKAGED_OPENGL_SMOKE_OK")
            print("Rendered and saved the synthetic alignment map, axes, labels, and colorbar update.")
            print("Verified viewer reuse and fail-closed rejection of a physical configuration.")
        elif UNAVAILABLE_MARKER in log:
            image = None
            outcome = "unavailable"
            print("PACKAGED_OPENGL_SMOKE_UNAVAILABLE")
            print("The packaged GUI could not create a usable OpenGL context on this Qt platform.")
            print("This is an explicit capability result, not rendering qualification.")
        else:
            raise RuntimeError(f"OpenGL smoke log contained no known result marker:\n{log}\n{combined}")
        if evidence_dir is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_path, evidence_dir / "opengl-smoke.log")
            if image is not None:
                shutil.copy2(image, evidence_dir / "smoke-opengl.png")
                shutil.copy2(framebuffer, evidence_dir / "smoke-opengl-framebuffer.png")
                shutil.copy2(colorbar, evidence_dir / "smoke-opengl-colorbar.png")
            (evidence_dir / "opengl-smoke.json").write_text(
                json.dumps(
                    {
                        "result": outcome,
                        "qt_platform": qt_platform,
                        "exit_code": result.returncode,
                        "executable": executable.name,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--platform", choices=("windows", "offscreen"), default="windows")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    try:
        run_smoke(args.executable, args.timeout, args.platform, args.evidence_dir)
    except (OSError, RuntimeError) as exc:
        print(f"PACKAGED_OPENGL_SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
