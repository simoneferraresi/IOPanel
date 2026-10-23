"""Compare row-wise NumPy CSV export with/without TextIOWrapper write-through.

Synthetic, hardware-free, same-process ABBA measurement; does not replace the
production exporter. The CSV format and full byte content are checked for each
mode and all measured runs include staged file creation, flushing and fsync.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np

HEADER = '# SchemaVersion: 2\n# Comment: "non-ASCII µ and newline\\nsecond line"'
FLOAT_FORMAT = "%.17g"


def _write_once(directory: Path, data: np.ndarray, *, write_through: bool) -> tuple[float, str]:
    path = directory / ("write-through.csv" if write_through else "buffered.csv")
    started = time.perf_counter()
    with path.open("wb") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", newline="", write_through=write_through) as stream:
            np.savetxt(stream, data, delimiter=",", header=HEADER, comments="", fmt=FLOAT_FORMAT)
            stream.flush()
            os.fsync(stream.buffer.fileno())
    elapsed = time.perf_counter() - started
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    return elapsed, digest


def run(*, points: int = 100_000, repetitions: int = 4) -> dict[str, object]:
    if points < 2 or repetitions < 2:
        raise ValueError("points and repetitions must both be at least two")
    rng = np.random.default_rng(177)
    data = np.column_stack([np.linspace(1500, 1600, points), *(rng.normal(size=points) for _ in range(4))])
    data[10, 1] = np.nan
    data[20, 2] = np.inf
    data[30, 3] = -np.inf
    results: dict[str, list[float]] = {"write_through": [], "buffered": []}
    digest: str | None = None

    with tempfile.TemporaryDirectory(prefix="iopanel-csv-write-") as temporary:
        directory = Path(temporary)
        # Warm both variants under the same filesystem/cache conditions.
        for write_through in (True, False):
            _, current_digest = _write_once(directory, data, write_through=write_through)
            if digest is None:
                digest = current_digest
            elif current_digest != digest:
                raise AssertionError("The two writers produce different archival CSV bytes")

        for index in range(repetitions):
            # Alternate order to reduce monotonic thermal/cache artifacts.
            order = (True, False) if index % 2 == 0 else (False, True)
            for write_through in order:
                elapsed, current_digest = _write_once(directory, data, write_through=write_through)
                if current_digest != digest:
                    raise AssertionError("Unexpected CSV byte mismatch")
                results["write_through" if write_through else "buffered"].append(elapsed)

    before = statistics.median(results["write_through"])
    after = statistics.median(results["buffered"])
    return {
        "points": points,
        "columns": 5,
        "repetitions": repetitions,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "os": platform.platform(),
        "writer": "NumPy savetxt %.17g; staged file, flush, fsync",
        "sha256_identical": True,
        "sha256": digest,
        "seconds": results,
        "medians": {"write_through": before, "buffered": after},
        "buffered_vs_write_through_percent": 100 * (after / before - 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=100_000)
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(points=args.points, repetitions=args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        f"F04 CSV write: {result['medians']}, relative={result['buffered_vs_write_through_percent']:+.1f}%, "
        f"identical={result['sha256_identical']}"
    )


if __name__ == "__main__":
    main()
