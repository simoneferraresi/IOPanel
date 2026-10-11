# MATLAB Engine shutdown: process-lifetime investigation

Status: **offline baseline investigation only** for [#129](https://github.com/simoneferraresi/IOPanel/issues/129). This document does not certify cleanup of a real MATLAB process or change production shutdown behavior.

## Existing implementation and guarantees

IOPanel uses \`MatlabEngineManager\` and \`matlab.engine.start_matlab(background=True)\`. The Qt loop polls the future and reuses a single Engine for FIG exports. At shutdown:

1. The Qt prewarm and poll timers stop and the manager transitions to \`SHUTTING_DOWN\`.
2. When Engine startup is still pending, a **daemon Python thread** calls \`FutureResult.cancel()\`, then, if cancellation was not successful, \`FutureResult.result()\`, followed by \`eng.quit()\` on a returned engine.
3. The GUI does not wait for this thread. Therefore, a normal window close **does not imply** the disposal has finished, and Python interpreter exit can abandon a still-running daemon thread.
4. During a normal running process, existing mocked tests show that, when a future resolves and yields an Engine, its \`quit()\` is attempted. The tests do **not** establish that the MATLAB OS process has actually exited.
5. MathWorks documents that normal Python exit stops an Engine it still owns; whether an **in-progress, never-obtained** Engine startup is covered by that guarantee in the project's R2024b/Engine 24.2.2 profile is **not established** by the current evidence.

Do not treat an Engine \`quit()\` return, a GUI close, or the end of the Python process as an independent observation of the MATLAB process having exited.

## Observations versus claims

| Evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| Window \`closeEvent\` completed | Qt GUI cleanup reached its terminal path | Python terminated or a pending MATLAB startup was disposed |
| \`app.exec()\` returned | Qt event loop ended | All daemon threads or MATLAB processes exited |
| Python subprocess exit code observed by its parent | The Python process ended | MATLAB's process exited |
| \`FutureResult.cancel()\` returned \`True\` | The implementation reported that its cancellation request succeeded | A generic guarantee about cancellation of **engine startup** on R2024b |
| \`eng.quit()\` was invoked | An Engine cleanup command was attempted | The external MATLAB process definitively exited |
| MATLAB child PID no longer exists, correlated to the owned startup | Observed process absence at the measurement time | Safety or status of an unrelated MATLAB process |

The driver-free \`tests/test_matlab_startup_process_lifetime.py\` launches a **fresh Python interpreter** in two controlled fake-future cases:

- **resolved**: shutdown returns promptly; the fake cancellation unblocks, the disposer calls \`engine.quit()\`, and the subprocess finishes after disposal was observed.
- **blocked**: shutdown returns, the fake future's cancellation remains blocked in the daemon, and the Python interpreter nevertheless exits. No \`engine.quit()\` event occurred before interpreter exit.

These tests neither launch MATLAB nor alter production code. The blocked case is a **known guarantee gap**, not a fix for it. Do not keep it as an expected failure after a future architecture claims to close the gap; change the test to assert the new required contract.

## Options considered

- **Make the disposal thread non-daemon:** does not lose it at Python exit, but a stuck \`cancel()\`, \`result()\` or \`quit()\` can keep the entire Python application alive indefinitely after the GUI closes. Not safe as a one-line change.
- **Join a daemon for a fixed timeout:** can collect a timely successful disposal and useful diagnostics, but does not guarantee disposal after timeout; joining on the Qt thread risks blocking the GUI. It is a bounded *best effort*, not deterministic resource ownership.
- **Dedicated Engine supervisor process:** could own startup and cleanup beyond GUI lifetime, but needs a supported, independently testable MATLAB Engine ownership/handoff protocol for FIG saves, process identity tracking and Windows process exit policy. A Python \`FutureResult\` cannot be assumed safely transferable to an already-running helper. This is an architectural proposal, **not implemented**.
- **Rely on vendor/Python automatic shutdown:** MathWorks documents behavior for an Engine owned by Python, but our specific pending-startup scenario and observed exit mechanism remain to be characterized on R2024b.

Any new architecture must preserve responsive window close, single-Engine reuse for FIG export, no duplicate Engine start, no GUI-thread blocking, and no termination of unrelated \`MATLAB.exe\` processes.

## Next evidence required

A separately approved **MATLAB-only** session on the remotely accessible lab PC (no CT400, cameras, piezos or scans) should record, using a **fresh process** and a *known supported R2024b environment*:

1. Parent Python PID, Engine-owned MATLAB child PID(s) and timestamps/identifiers when available. Do not guess the PID based solely on an image name.
2. Qt window close timestamp; \`app.exec()\` return; Python process exit; engine startup/disposal logs; correlated MATLAB child disappearance (or persistence).
3. At least: shutdown before start; shutdown during STARTING; shutdown after READY; successful FIG export then shutdown; optionally a vendor-approved startup failure without intentionally disrupting the lab.
4. Run repeats only with operator approval and bounded observation. Never kill all \`MATLAB.exe\` instances or treat a machine-wide MATLAB process count as proof of ownership.
5. Verify that any proposed change behaves as documented on the real supported Engine before claiming Issue #129 complete.

## Reference documentation

- [MathWorks: start_matlab](https://www.mathworks.com/help/matlab/apiref/matlab.engine.start_matlab.html)
- [MathWorks: FutureResult.result](https://www.mathworks.com/help/matlab/apiref/matlab.engine.futureresult.result.html)
- [MathWorks: FutureResult.cancel](https://www.mathworks.com/help/matlab/apiref/matlab.engine.futureresult.cancel.html)
- [MathWorks: Start and stop Engine](https://www.mathworks.com/help/matlab/matlab_external/start-the-matlab-engine-for-python.html)

The documentation may describe newer MATLAB releases; **validate version-specific behavior for R2024b/24.2.2**, rather than assuming all releases have identical cancellation guarantees.
