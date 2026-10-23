# F05 — schema-v2 CSV importer optimization (2026-10-23)

## Scope

This change replaces the common numerical CSV parsing path in `logic/scan_import.py` with NumPy's native `loadtxt` parser. Metadata validation, file-size limit, detector ordering, numerical meaning, immutable `ImportedScan` values, and MAT import remain unchanged. A failed or nonconforming fast parse rewinds to the existing strict `csv.reader` implementation so that unusual valid records and precise user-facing errors retain their historical behavior.

The change does **not** modify camera, CT400, laser, piezo, MATLAB Engine, Qt worker lifetime, staged file publishing or on-disk schema.

## Evidence

Windows CI evidence for draft [PR #244](https://github.com/simoneferraresi/IOPanel/pull/244): [run 39935820744, F05 scan-I/O artifact](https://github.com/simoneferraresi/IOPanel/actions/runs/39935820744/artifacts/12494310150).

- Candidate PR head at capture: `818dac8fb67730a8a1bdd835ea2d2f56c10806ed` (GitHub Actions checked out synthetic merge SHA `4ab202e5a7ff93dad4e936267a2abca3d1e5d20d`).
- Windows Server 2025 x64 runner, Python 3.12.10, NumPy 2.5.3, PySide6 6.12.0, Qt offscreen. Synthetic acquisition; zero physical hardware opened or operated.
- 10k and 100k wavelengths, four active optical detectors; one warmup and two measured subprocesses, repeatable `tools.performance_baseline capture-io`.
- The historical pre-optimization benchmark from [scan I/O characterization](scan-io-memory-characterization.md) was taken on a different Windows host/dependency environment. Values **must not be interpreted as a strictly controlled paired speedup**.

| 100k wavelengths, 4 detectors | Historical median | F05 candidate median | Interpretation |
| --- | ---: | ---: | --- |
| CSV import wall time | 1.92 s | 0.463 s | Approximately 4.1× lower observed time; cross-environment |
| CSV import process CPU | 1.91 s | 0.461 s | Expected with native-code parsing; cross-environment |
| CSV import Python traced peak | 13.06 MiB | 13.06 MiB | No clear traced-memory regression in these measurements |
| Malformed CSV rejection | 1.66 s | 1.93 s | Fallback intentionally keeps strict, slower Python-level validation |
| Staged CSV export | 5.61 s | 5.13 s | Exporter unchanged; unrelated environment variation |
| MAT import | 0.0398 s | 0.0378 s | MAT importer unchanged |

At 10k wavelengths / four detectors, F05 CSV import median was 0.0475 s in the dedicated capture. An independent Windows benchmark run (39935820762, three repetitions) reported 0.0442 s for 10k / four detectors and 0.2154 s for 50k / four detectors.

## Acceptance and risks

- New regression tests assert ordinary files use the fast path, binary64 values and NaN/Inf stay intact, original detector order is preserved, and valid multiline/quoted inputs and empty comma-records still import.
- Missing/inconsistent rows, invalid numerics, no samples, invalid metadata and other malformed files retain legacy error categories through the strict fallback.
- Both fast and fallback paths operate on the original stream; the header uses `readline()` to preserve the position for `seek()`.
- The fast path has not been tested with a real CT400-generated archive corpus from the lab. This is not required to justify driver-free CI correctness but remains useful as an operator-supplied compatibility sample (do not run hardware to obtain one).
- Genuine before/after same-host measurements would strengthen the speedup attribution; the underlying tool and exact source revisions are recorded for reproduction.

This report records performance evidence, not a guarantee of disk, hardware, network, or GUI latency improvement.
