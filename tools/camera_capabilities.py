"""Read-only GenICam capability report for one explicitly selected camera.

Example: ``uv run python tools/camera_capabilities.py --camera-id DEV_123``
This tool never starts acquisition or writes camera/NIC settings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hardware.camera_capabilities import inspect_camera


def _identity(camera: Any, method: str) -> Any:
    try:
        fn = getattr(camera, method, None)
        return fn() if callable(fn) else None
    except Exception:  # noqa: BLE001 - SDK metadata getters are optional.
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera-id", required=True, help="Exact Vimba camera identifier; required, no all-camera mode"
    )
    args = parser.parse_args()
    try:
        from vmbpy import VmbSystem
    except Exception as exc:  # noqa: BLE001 - binding/runtime import failures vary by SDK install.
        print(f"VmbPy unavailable: {exc}", file=sys.stderr)
        return 2

    with VmbSystem.get_instance() as system:
        camera = system.get_camera_by_id(args.camera_id)
        with camera:
            report = inspect_camera(camera)
            report["diagnostic_mode"] = "read-only"
            report["acquisition_started"] = False
            if report["identity"].get("id") is None:
                report["identity"]["id"] = _identity(camera, "get_id")
            print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
