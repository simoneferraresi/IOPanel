"""Write a reproducible, machine-readable inventory of a PyInstaller folder."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import PyInstaller


def classify(path: Path) -> str:
    parts = path.parts
    name = path.name.lower()

    if parts[0:2] == ("_internal", "PySide6"):
        if len(parts) > 2 and parts[2] == "plugins":
            return "Qt plugins"
        if len(parts) > 2 and parts[2] == "translations":
            return "Qt translations"
        return "Qt/PySide6"
    if name.startswith("python3") and path.suffix.lower() == ".dll":
        return "Python runtime"
    if name == "base_library.zip" or parts[:1] == ("_internal", "encodings"):
        return "Python standard library"
    if parts[:1] == ("_internal",) and parts[1:2] == ("cv2",):
        return "OpenCV"
    if parts[:1] == ("_internal",) and parts[1:2] in (("numpy",), ("numpy.libs",)):
        return "NumPy"
    if parts[:1] == ("_internal",) and parts[1:2] in (("scipy",), ("scipy.libs",)):
        return "SciPy"
    if parts[:1] == ("_internal",) and parts[1:2] == ("PySide6",):
        return "Qt/PySide6"
    if parts[:1] == ("_internal",) and parts[1:2] == ("shiboken6",):
        return "Qt/PySide6"
    if path.suffix.lower() in (".dll", ".pyd", ".exe"):
        return "Other native binaries"
    return "Other files"


def git_revision(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_inventory(distribution: Path, archive: Path | None = None) -> dict[str, object]:
    distribution = distribution.resolve()
    root = distribution.parent.parent
    files = []
    categories: defaultdict[str, int] = defaultdict(int)
    directories: defaultdict[str, int] = defaultdict(int)

    for source in sorted(path for path in distribution.rglob("*") if path.is_file()):
        relative = source.relative_to(distribution)
        size = source.stat().st_size
        files.append({"path": relative.as_posix(), "bytes": size})
        categories[classify(relative)] += size
        parent = relative.parent
        while parent != Path("."):
            directories[parent.as_posix()] += size
            parent = parent.parent

    try:
        lock_sha256 = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    except OSError:
        lock_sha256 = None

    archive_bytes = archive.stat().st_size if archive is not None else None
    return {
        "git_revision": git_revision(root),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pyinstaller": PyInstaller.__version__,
        "uv_lock_sha256": lock_sha256,
        "distribution": distribution.name,
        "extracted_bytes": sum(item["bytes"] for item in files),
        "archive_bytes": archive_bytes,
        "file_count": len(files),
        "categories_bytes": dict(sorted(categories.items())),
        "directories_bytes": dict(sorted(directories.items())),
        "largest_files": sorted(files, key=lambda item: (-item["bytes"], item["path"]))[:50],
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", type=Path, help="PyInstaller one-folder output directory")
    parser.add_argument("--archive", type=Path, help="Optional compressed artifact to measure")
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")
    args = parser.parse_args()

    inventory = build_inventory(args.distribution, args.archive)
    result = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
