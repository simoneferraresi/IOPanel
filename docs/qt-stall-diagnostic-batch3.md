# Windows Qt stall diagnostic — Batch 3

## Scope and setup

This opt-in diagnostic repeats simulated `MainWindow` construction and shutdown using the native Windows Qt platform. The configuration contains two simulated cameras and a simulation CT400 backend. Before construction it disables Vimba startup and MATLAB Engine availability; the configuration has no piezos. No physical device APIs are opened.

The independent supervisor launches pytest as a child process and records its executable, command line, PID, parent PID, UTC creation time, owner, last pytest/progress output, stdout, stderr, deadline and final exit status. At an external timeout it rechecks the PID creation time before attempting a Microsoft ProcDump triage capture. It records missing tools, PID changes, capture timeouts, access denials and other failures explicitly.

## Local Windows campaign — 2026-10-13

Environment: Python 3.12.8, PySide6/Qt 6.11.2, pytest 9.1.1, `QT_QPA_PLATFORM=windows`; base commit `4b5f205999a0596aa32496eac40937c263519f51`.

The first corrected 20-cycle run reproduced one persistent stall. Cycles 1–13 completed. Cycle 14 began at monotonic timestamp `1274790.359` and did not complete before the 300-second external deadline. Pytest's 30-second faulthandler snapshot showed:

```text
ParameterControl.setValue
  -> ParameterControl.__init__
  -> CameraPanel._init_ui
  -> CameraPanel.__init__
  -> MainWindow._create_camera_panel
  -> MainWindow._init_cameras_lazy
  -> MainWindow._begin_lazy_init
```

The other thread had no Python frame. The supervisor terminated the pytest process and returned a timeout result; this run did not pass. Its child identity was PID `19952`, creation time `2026-10-13T07:34:10.1834200Z`, executable `.venv/Scripts/python.exe`, command `python -u -m pytest -vv -s -o faulthandler_timeout=30 tests/test_qt_stall_stress.py`.

After installing and validating Microsoft-signed ProcDump 12.01, two further 20-cycle runs completed cleanly (40 additional cycles). Therefore the construction stall remains intermittent. ProcDump's `-mt` triage mode was verified against a disposable process waiting in the Windows native `Sleep` API: PID and creation time matched before/after, and a 65 KB triage dump was written and recognized from ProcDump's completion marker. No external dump was captured from the IOPanel stall because it did not recur in those two runs.

The actual Qt trace localizes the stall to native value setup during exposure-control construction, but does not prove that starting the first camera worker before constructing later placeholders caused it. No production initialization order or camera ownership code was changed. Keep issue #89 open.

## Artifacts and workflow

The local first-timeout manifest, progress log and raw pytest output are retained at:

`%LOCALAPPDATA%\Temp\iopanel-b3-qt-native-rerun\`

The two clean ProcDump-enabled runs are at `%LOCALAPPDATA%\Temp\iopanel-b3-qt-procdump-run\` and `%LOCALAPPDATA%\Temp\iopanel-b3-qt-procdump-run-2\`. A successful independent ProcDump permission probe is at `%LOCALAPPDATA%\Temp\iopanel-b3-procdump-probe\`.

The opt-in workflow is `.github/workflows/qt-stall-diagnostic.yml`. It installs ProcDump from the Microsoft Sysinternals download, verifies the Authenticode signature, and uploads run evidence even when the supervised test fails. Ordinary full-suite runs do not execute the stress; the diagnostic test is skipped unless the workflow supplies its explicit configuration path.
