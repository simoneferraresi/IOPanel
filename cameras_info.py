"""Optional, operator-invoked VmbPy camera inventory (no camera streaming).

Unlike the full IOPanel GUI, this script only enters the vendor discovery
system and lists its detected cameras. It requires a working Vimba X install.
Nothing contacts the camera SDK merely by importing this module or using --help.
"""

from __future__ import annotations

import argparse
import sys


def list_cameras() -> None:
    """Print the vendor's camera descriptions while its system context is open."""
    # Optional VmbPy is intentionally imported only by an explicit invocation.
    from vmbpy import VmbSystem

    with VmbSystem.get_instance() as system:
        for camera in system.get_all_cameras():
            print(camera)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List Vimba X cameras without starting an IOPanel acquisition.")
    parser.parse_args(argv)

    try:
        list_cameras()
    except Exception as exc:
        # Discovery can fail at VmbPy import or at runtime when a native
        # transport layer is absent. Never silently fall back to simulation.
        print(f"Camera inventory unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
