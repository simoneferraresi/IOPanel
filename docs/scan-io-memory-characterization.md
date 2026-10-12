# Batch 2 scan I/O memory characterization

The raw versioned report is [`performance-evidence/scan-io-batch2.json`](performance-evidence/scan-io-batch2.json). It records the source commit `2f04f99a369f48e86f01a2b7e0327f9104d5bb10`, Python/dependency lock, Windows/Qt context, all raw samples and median/p95/min/max distributions. The 10k/50k/100k point × one/four detector matrix ran with one warmup and three fresh measured subprocesses per matrix point after PR #175 merged.

The fixtures carry a 91.8 KB multiline comment and include NaN/Inf detector values. CSV and MAT round trips verify detector ordering, dimensions, comment text and numeric samples. A separate malformed CSV row is rejected by the production importer. The real staged writer is used for CSV/MAT output; it includes staging, flush/fsync and atomic publication. Thus the benchmark measures the current CSV streaming path, not an additional `StringIO` copy. The post-#173 streaming importer was measured without redesign.

At 100,000 points and four detectors, medians across three subprocesses were:

| Operation | Wall median (p95 / max) | Process CPU median | Python traced peak | File bytes | Throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| Build export payload | 3.8 ms (4.2 / 4.2 ms) | below clock resolution | 4.00 MiB | — | — |
| Staged CSV export | 5.61 s (5.63 / 5.63 s) | 5.59 s | 0.19 MiB | 5,694,084 | 0.97 MiB/s |
| CSV import | 1.71 s (1.80 / 1.80 s) | 1.70 s | 13.06 MiB | 5,694,084 | 3.17 MiB/s |
| Staged MAT export | 331.8 ms (345.4 / 345.4 ms) | 313 ms | 3.24 MiB | 2,884,157 | 8.29 MiB/s |
| MAT import | 39.8 ms (40.0 / 40.0 ms) | 47 ms | 13.06 MiB | 2,884,157 | 69.19 MiB/s |
| Malformed CSV rejection | 1.73 s (1.81 / 1.81 s) | 1.73 s | 8.83 MiB | 5,694,042 | 3.13 MiB/s |

The process peak working set for the measured subprocesses ranged up to 110.8 MiB; this is a process-lifetime high-water mark, not the operation's transient allocation. Reported current working set after each operation was 87–92 MiB. `tracemalloc` peaks exclude native NumPy/SciPy allocations. The CSV writer's small traced peak and direct staged stream show no additional complete CSV string buffer in the current exporter, so no exporter code change was warranted. Disk timing depends on this machine's local temporary filesystem; it does not predict instrument-save latency on another disk.

Reproduce on Windows with the locked project environment:

```powershell
python -m tools.performance_baseline capture-io `
  --output scan-io-batch2.json `
  --points 10000 50000 100000 --detectors 1 4 `
  --repetitions 3 --warmups 1 --qt-platform offscreen
```

No CT400, camera, piezo or MATLAB Engine was initialized. Focused scan-I/O and report tests: **16 passed**.
