# F06 stage 2: NumPy-backed FIG preparation (2026-10-23)

This NumPy FIG optimization originated as stage-2 PR #251 stacked on stage-1 PR #247. Stage 1 has since been merged as `8c95c4015e711effa7f43640bcc0c840e96269dc`. On 2026-10-23 the laboratory operator reported native real-MATLAB acceptance PASS for the exact pre-rebase stage-2 head `c7c8afcdd004c8a09ba251d215576eae1f5ed094` on MATLAB R2024b Update 4. Its timestamped JSON results, FIG files and logs remain in the lab validation workspace; they have not been independently uploaded or inspected through GitHub.

## Design

`ScanMeasurement` owns detached NumPy copies and sets their writable flag to false during its construction. Instead of turning these arrays into nested Python lists and a large JSON string on the GUI thread, `build_prepared_matlab_fig_payload` retains read-only NumPy views, detector label/order and 3-component RGB colors. The worker owns the prepared object before entering QThread. Qt queues **four strings** to the new slot; it does not serialize or marshal Python arrays through Qt's meta-object arguments.

The worker converts the arrays to required Python lists in its **own thread** and shares the existing MATLAB plotting/health check/error/cleanup code. The five-string JSON worker slot and JSON helper remain available for compatibility. This change does not eliminate conversion costs inside MATLAB Engine or its Python bindings; it moves the list conversion and avoids JSON round-trips on the GUI thread.

## Reproducible Windows synthetic evidence

[CI run 39940067603](https://github.com/simoneferraresi/IOPanel/actions/runs/39940067603), [original machine-readable data](https://github.com/simoneferraresi/IOPanel/actions/runs/39940067603/artifacts/12495207507). Synthetic 100,000 wavelengths, four detectors, NaN/Inf, NumPy 2.5.3, four timed calls per variant after warmup.

| GUI-thread payload preparation only | Median wall time | Approximate traced peak difference |
| --- | ---: | ---: |
| JSON construction | 215.830 ms | 27,035,739 bytes (25.8 MiB) |
| Prepared readonly view metadata | 0.011 ms | 1,833 bytes (1.8 KiB) |

The legacy JSON payload is 10,217,092 UTF-8 bytes (9.75 MiB). These medians are not complete FIG export times, and their ratio is **not** a claim of end-to-end MATLAB speedup. Tracemalloc samples estimate Python-traced allocations only and exclude NumPy native allocations and memory retained by the surrounding GUI. The prepared payload itself adds no new NumPy data copy by design.

Reproduce in a driver-free checkout using:
```powershell
uv run python -m tools.benchmark_matlab_fig_payload --points 100000 --repetitions 4 --output f06-fig-payload.json
```

The branch-specific CI capture step was removed after evidence collection.

## Qualification and integration

- Operator-reported real MATLAB acceptance for stage 1 and stage 2 passed using synthetic scans, MATLAB R2024b Update 4, MATLAB Engine for Python 24.2.2, Windows 10 and Qt/PySide6 6.12.0. Stage-2 acceptance also tested the legacy JSON slot and real figure-content comparison against stage 1. The artifacts are local to the laboratory PC.
- This rebased commit reapplies only the stage-2 changes over merged stage-1 `main`, preserving unrelated changes that landed meanwhile. The merged source must pass current Windows CI/packaging checks prior to merge.
- The existing GUI `PlotWidget.cleanup()` forceful QThread termination fallback predates F06 and remains an independent, unqualified lifecycle risk.
