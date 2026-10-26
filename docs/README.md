# IOPanel documentation

Start with the [project homepage](../README.md) for the application overview,
screenshots, and a short driver-free or laboratory setup route.

## Getting started and daily use

- [Technical guide](technical-guide.md) — installation, hardware profiles,
  configuration, data handling, operator workflows, and development
- [Windows one-folder packaging](windows-packaging.md) — frozen distribution,
  simulation-only limitations, and writable data paths
- [Saved scan overlay performance](scan-overlay-performance.md) —
  multi-scan comparisons and profiling

## Hardware and laboratory qualification

- [Supervised hardware validation protocol](laboratory-hardware-validation.md) —
  what must be checked on the real lab PC
- [Camera compatibility profiles](vmbpy-compatibility.md) —
  Vimba X/VmbPy validation and rollback profiles
- [Camera performance and image quality](camera-performance.md)
- [PM5020 power-meter backend](pm5020-backend.md)
- [CT400 supervised campaign](ct400-supervised-campaign.md)
- [Standalone instrument discovery](standalone-device-discovery.md)
- [Laboratory qualification record](laboratory-validation-report.md)

## Diagnostics, performance, and development

- [Power-monitor recordings](power-monitor-recordings.md)
- [Scan I/O memory characterization](scan-io-memory-characterization.md)
- [Performance baselines](performance-baselines.md)
- [Qt test lifecycle standards](qt-test-lifecycle-standards.md)
- [Windows package optimization](windows-package-optimization.md)
- [Contribution guidelines](../CONTRIBUTING.md)

Additional audit reports, project milestones, and research-specific
investigations remain in this directory. Their conclusions are scoped to
the experiments, software versions, and dates they document.

**Important:** driver-free tests, screenshots, and simulated behavior must
never be represented as validation of physical hardware.
