# F06 stage-1 licensed-MATLAB acceptance — 2026-10-23

This note records operator-reported, hardware-free acceptance on a Windows 10 laboratory PC for the exact production source head `3eefc2b6cd23ba3c1a056721d8433c8808da79b3` (PR #247). It is not an independently downloaded audit of the local files.

Environment: MATLAB R2024b Update 4; MATLAB Engine for Python 24.2.2; CPython 3.12.8 x64; PySide6 / Qt 6.12.0; NumPy 2.5.3.

A corrected synthetic-data acceptance runner was invoked from ordinary PowerShell (not the Codex execution sandbox) through `run_f06_real_matlab_qualification.ps1`. The launcher reported `stage1 PASS` for the tested commit, including real FIG creation/reopen/content checks, expected invalid-destination handling, Qt worker/QThread retirement and Engine shutdown. Its local result directory is `results/stage1_real_20261023_143504_646107` within `C:\Users\bellanca\Desktop\IOPanel-F06-Validation`, with timestamped stdout and stderr logs and FIG files; these local artifacts were not uploaded into this repository.

CI already passed at the production source head: [hardware-independent Windows tests](https://github.com/simoneferraresi/IOPanel/actions/runs/39937528999) and [Windows package](https://github.com/simoneferraresi/IOPanel/actions/runs/39937529491). No physical equipment was operated.

This documentation-only change does not modify the MatlabSaveWorker implementation, the tested FIG export logic, the pinned dependencies or the lab acceptance evidence. The pre-existing `PlotWidget.cleanup()` QThread forced-termination fallback is outside F06 and remains a separate follow-up.
