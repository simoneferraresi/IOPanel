# Python reference and Qt reachability audit — 2026-10-23

## Scope, baseline and limitations

- Repository: simoneferraresi/IOPanel; audited baseline main after squash-merging PR #241: afa993ddaa67e4020f8d4b38c1bf6b37c003c5fc.
- Audit branch: audit/python-reference-inventory. F07 was independently merged in PR #242 (commit `8c73bd17fc4e2b805db69726bf7dbbe39f9c2046`) after the inventory baseline was captured.
- Method: Python standard-library AST scan of the entire source tree, including tests and tools, using tools/audit_reference_inventory.py; runs on the hardware-independent Windows CI runner without vendor drivers, MATLAB or physical instruments.
- Evidence: [Actions reference inventory](https://github.com/simoneferraresi/IOPanel/actions/runs/39933808668/artifacts/12491994798), containing reference-inventory.json and reference-inventory.md (artifact retention: 14 days).
- This is a **reference inventory**, not a complete, typed/dynamic Python call graph. A matching identifier may refer to a different symbol; absence of a syntactic reference cannot rule out callbacks, reflection, dependency injection, runtime slots, inherited virtual methods or outside callers.

## Inventory counts

| Measure | Result |
| --- | ---: |
| Python source files | 228 |
| Successfully parsed | 228 |
| Parse failures | 0 |
| Classes, functions, methods and signals inventoried | 3,775 |
| Approximate local import edges | 622 |
| Qt connect / dispatch call sites | 581 |
| Reflection and dynamic lookup call sites | 796 |
| Definitions with zero matched name, attribute or static-lookup references | 21 |

These numbers describe the audited branch, which has additional audit tool/test modules but not the F07 deletion. Re-run after each proposed change; do not subtract candidate counts and assume dead code was removed.

## Twenty-one findings and proposed disposition

The scanner reports **candidates for manual review**, never deletions authorized by these numbers alone.

| Candidate and location | Interpretation / recommended next step |
| --- | --- |
| ui/camera_widgets.py:108 ImageConversionSignals | **Resolved:** high-confidence unused definition removed in merged PR #242 after passing Windows CI. Remains in this historical pre-removal inventory. |
| ui/control_panel.py:700 QABCMeta | **Investigate:** no direct uses; contains a custom QWidget/ABC metaclass and abstract-instantiation guard. Check class contracts and specialized tests before removal. |
| hardware/camera_capabilities.py:296 capability_dict | **Retain pending API review:** documented compatibility alias for diagnostic callers. External use cannot be excluded. |
| ui/application_icons.py:19 application_icon_names | **Low-risk candidate:** helper returning constant icon metadata; confirm documentation/plugin use before deletion. |
| config_model.py:67 InstrumentsConfig.validate_edfa_resource | **Retain:** Pydantic field validator is registered by decoration, not a direct Python call. **Not dead code.** |
| hardware/camera.py:696 VimbaCam.set_roi | **Retain pending interface review:** hardware/SDK-facing public method. Do not remove based on app-internal call coverage. |
| hardware/ct400.py:235 CT400.get_number_inputs | **Retain pending API review:** public device capability query. |
| hardware/ct400.py:247 CT400.get_number_detectors | **Retain pending API review:** public device capability query. |
| hardware/ct400.py:259 CT400.get_CT400_type | **Retain pending API review:** public device type query. |
| hardware/ct400.py:344 CT400.set_bnc | **Retain pending hardware review:** public BNC detector configuration API; removal would alter physical-device functionality. |
| hardware/dummy_pm5020.py:111 DummyPM5020.set_power_level | **Retain pending simulation API review:** method for test/manual simulated power injection, even if current tests do not invoke it. |
| hardware/piezo.py:107 PiezoController.find_devices | **Retain pending API review:** static SDK discovery helper; never run discovery during the audit. |
| logic/matlab_engine_manager.py:77 MatlabEngineManager.prewarm_timer_active | **Low-risk candidate:** unused status/property convenience API; review test/diagnostic consumers. |
| ui/alignment_panel.py:362 AlignmentPanel.disable_all_buttons | **Review legacy compatibility:** delegates to the current ownership-aware control refresh, potentially intentional external API. |
| ui/log_console.py:192 LogHighlighter.highlightBlock | **Retain:** overrides Qt's virtual highlighter callback, invoked by Qt rather than by an explicit call. **Not dead code.** |
| ui/log_console.py:227 DiagnosticsHighlighter.highlightBlock | **Retain:** second Qt virtual highlighter callback. **Not dead code.** |
| ui/log_console.py:469 LogConsole.copy_selected | **Low-risk candidate:** inspect QAction/menu registration and external use; the other copy helpers remain active. |
| ui/main_window.py:2454 MainWindow._create_and_open_camera | **Investigate carefully:** possible old synchronous physical-camera open path superseded by worker-based initialization. Read-only source investigation first; do not call it, change device ownership or delete without native lifecycle evidence. |
| ui/plot_widgets.py:398 PowerMonitorTraceWidget.raw_elapsed_values | **Low-risk candidate:** property returning an existing list; could be intended diagnostics API. |
| ui/plot_widgets.py:403 PowerMonitorTraceWidget.raw_detector_values | **Low-risk candidate:** property returning an existing detector-data dictionary; check external users. |
| ui/plot_widgets.py:1237 PlotWidget.matlab_save_status_update | **Candidate:** declared Qt signal with no identified emits/connections; verify string/meta-object connections before proposing removal. |

## F07 resolution

PR [#242](https://github.com/simoneferraresi/IOPanel/pull/242) was squash-merged on 2026-10-23 as `8c73bd17fc4e2b805db69726bf7dbbe39f9c2046`. It removed only the unused ImageConversionSignals container and corrected the obsolete QRunnable description. The production converter's signal definitions, latest-frame mailbox, conversion, QThread ownership and callback semantics remain unchanged. The 21-item historical inventory now includes one resolved candidate; do not interpret a subtraction as an independently rerun post-merge audit.

## Further investigation needed for a complete call graph

1. Resolve call expressions to qualified targets where possible (including local methods, constructor calls, imported functions, inherited methods), with unresolved/ambiguous edges reported explicitly rather than guessed.
2. Record Qt signal-to-slot connections and QObject context, and link static string dispatch to registered callbacks. The current inventory counts these sites but does **not** resolve them into a complete connected graph.
3. Supplement static analysis with bounded, driver-free application/test coverage on Windows for callback and dynamic paths, without initializing physical devices. Externally callable APIs and behavior reachable only through hardware remain unproven without authorized operator validation.
4. Manually review every zero-reference candidate against documentation and external APIs; do not batch-delete.
5. Keep separate, tiny regression-checked PRs. Prioritize pure UI/documentation leftovers; hold device API, MATLAB Engine, and Qt lifecycle behavior until independent evidence exists.

No real equipment was discovered, initialized or operated during this audit. No operational Python implementation was modified on this branch.
