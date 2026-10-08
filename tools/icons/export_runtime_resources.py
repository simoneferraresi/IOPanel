"""Copy the approved icon generator's PNGs into the Qt runtime resource tree."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_IDS = ("optical_burst", "prism_spectrum")


def main() -> None:
    generated = ROOT / "assets" / "icons" / "png"
    runtime = ROOT / "resources" / "icons" / "app"
    for icon_id in ICON_IDS:
        for size in SIZES:
            source = generated / icon_id / f"{size}.png"
            payload = source.read_bytes()
            if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"Not a PNG: {source}")
            width, height, bit_depth, color_type = struct.unpack_from(">IIBB", payload, 16)
            if (width, height, bit_depth, color_type) != (size, size, 8, 6):
                raise ValueError(f"Expected {size}x{size} 8-bit RGBA PNG, found {source}")
            target = runtime / icon_id / f"{size}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            print(f"Wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
