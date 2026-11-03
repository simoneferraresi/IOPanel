# M4-A EDFA GPIB reliability: forensic report and operator qualification

**Status:** Diagnostic investigation. No evidence yet establishes that the physical GPIB link is reliable enough for EDFA parameter writes. Do not merge PR #296 or enable physical setters on the basis of read-only probe successes.

## Physical evidence and scope

Device: IPG EAD-2K-C Rev 1.1.175, GPIB0::12::INSTR, NI-VISA via PyVISA on Windows. Manufacturer command table: IPG P21-006000 Rev A pp. 27–32. Original M4-A feature head: ba988315e5d587dac7ef4435a1d15634182e192f. Original main: 3f8c112262619b45f36af42d673fce97776757e6.

NI I/O Trace binary Capture(2).nitrace (NI Spy 17.0 capture) was inspected for API call labels and query/response payloads. The directly recoverable sequence is:

| Sequential VISA session | viWrite / viRead | Outcome |
| --- | ---: | --- |
| CLI standalone telemetry | 9 / 9 | Success; instrument and resource-manager handles closed |
| CLI telemetry and configuration | 16 / 16 | Success; instrument and resource-manager handles closed |
| GUI telemetry and configuration | 14 / 13 | Failure during viWrite for SOUR:GAIN:SPO? |

**Total: 39 viWrite operations and 38 viRead operations.** These are SCPI query-string writes; none is a new physical M4 parameter-setting command. The last successful configuration reply in the final session was SOUR:CURR:LIM:HIGH? = 2.400. SOUR:GAIN:SPO? succeeded earlier in the same capture, reporting 37.0. The last recorded VISA status is the signed code -1073807265 (0xBFFF005F), VI_ERROR_NLISTENERS.

The capture ends at the failed operation. It does not independently show whether later cleanup succeeded. Native session handles, timestamps, precise command spacing, PID/TID and electrical line states have not been completely decoded from the proprietary file. Do not invent these details.

Additional Windows logs separately show the same code while sending SOUR:POW:STAT? and SOUR:POW:SPO?, plus a CLI failure during read-only telemetry after a previous successful CLI snapshot. These observations show that the error is intermittent, occurs during queries, and is **not exclusively caused by the GUI**.

To preserve complete trace context, open the capture in NI I/O Trace on the laboratory PC, choose File > Export, export TXT (include extended details if offered) and CSV, and retain the original binary. NI documentation: https://www.ni.com/docs/en-US/bundle/ni-io-trace-help/page/topics/exporting-captured-data-as-a-txt-or-csv-file.html

## Architecture audit

- The physical backend creates one ResourceManager on explicit Connect, opens a configured instrument resource, sets timeout and terminations, and owns its close. Its RLock serializes calls within the backend.
- The Qt monitor worker serializes its telemetry timer, manual configuration readback and supervised write operations in one worker; configuration is blocked while monitoring.
- The CLI uses the same physical backend with the same configured VISA timeout and terminations. It differs from the GUI in session lifetime and read sequence.
- Read-only telemetry and configuration each include a final status-consistency query. A communication failure latches ERROR until explicit cleanup/reconnect. Those checks must not be removed.
- No automatic physical write retry, competing EDFA worker query, or premature close immediately preceding the observed failure is established from code and trace alone.

## Interpretation and ranked investigation priorities

NI defines VI_ERROR_NLISTENERS as a no-listener condition involving NRFD and NDAC handshake lines. Unlike VI_ERROR_TMO waiting for a response, this error arises during the bus write. No standalone timeout increase is justified. Reference: https://knowledge.ni.com/KnowledgeArticleDetails?id=kA00Z000000P9v2SAC

1. Bus electrical/cable/controller or EDFA listener fault: plausible, high priority for isolation tests; not proven.
2. Instrument listener state, firmware behavior or transient controller state: plausible; requires independent NI-MAX/controller tests.
3. Query pacing or repeated session open/close: testable hypotheses, but no manufacturer timing requirement has been established.
4. External shared-bus/process interference: possible; record bus topology, ownership and controller hardware.
5. Pure GUI concurrency defect: less likely to explain CLI failures.
6. Permanently wrong address or invalid SOUR:GAIN:SPO? syntax: inconsistent with repeated successful replies.

## Software change scope

The new physical-session observer records resource, connection sequence number, query index and text, thread identity, monotonic elapsed time, VISA signed/hex error code, and session lifecycle. On controlled writes it records only the command prefix, without numeric setpoint arguments. It makes no new VISA command, does not alter native session ownership, and does not retry.

The operator-gated CLI in tools/edfa_gpib_diagnostics.py uses the existing reader and physical backend. Its VISA wrapper accepts only the existing SCPI query allowlist, rejects all direct setter writes, enforces a total query budget and time budget, and stops on the first error. It supports one to three sequential sessions and an explicitly selected 0/10/50/100 ms inter-query delay (0 by default). Its JSON report records each attempted command and result, session lifecycle, and any VISA errors. Reports are for diagnostic evidence, not optical safety certification.

## Operator-gated experiments (physically unqualified until performed)

**Default dry run (no VISA session):**

    uv run --no-sync python -m tools.edfa_gpib_diagnostics --config C:\path\reviewed-readonly.ini

Physical operation requires an independently approved instrument status, optical routing, isolation, controller/address identification, absence of other GPIB owners, and an authorized operator. A read-only VISA connection may itself change an instrument's remote/local state.

**One session, telemetry plus configuration, no pacing (16 expected queries), count limit 20:**

    uv run --no-sync python -m tools.edfa_gpib_diagnostics --config C:\path\reviewed-readonly.ini --connect-readonly --operator-confirmed --plan telemetry-configuration --sessions 1 --max-queries 20 --max-seconds 40 --interquery-delay-ms 0 --report-dir C:\path\reports

After review and separate approval, compare a two-session plan with the same commands and count limit 40. In a separate experiment, keep one session and compare 0 ms versus 50 ms. Change one variable at a time, limit attempts, and stop immediately on any VISA error. No automatic retries, no instrument reset, no interface clear, and no optical emission or parameter-setting command are permitted.

Record exact GPIB controller model and firmware, NI-VISA/NI-488.2 driver versions, Windows/PyVISA versions, shared devices and cable topology, NI-MAX communication result, and complete I/O Trace export. Physical rewiring is subject to laboratory equipment-handling approval.

## Acceptance decisions

- **Read-only diagnostics:** eligible for individually operator-approved bounded tests after safety/setup checks.
- **Physical supervised setpoint writes:** NOT approved; intermittent no-listener failures remain unexplained.
- **M4-A merge with physical writes disabled:** separate code/safety review and explicit operator decision; successful read-only queries alone do not resolve the fault.
- **Emission control:** not qualified and excluded.

The original laboratory script's 'All selected stages completed' must never be treated as GUI hardware acceptance if the GUI log shows an EDFA VISA exception. A manual GUI pass must be separately attested and logged. A successful native VISA close never demonstrates optical emission OFF.


## Windows PowerShell 5.1 acceptance: two separate laboratory failures (2026-11-03)

The first operator-run `-Stage Offline` attempt at `d274d61` exited after confirming the pinned worktree. Windows PowerShell 5.1 with `$ErrorActionPreference = 'Stop'` interpreted `uv`'s informational native stderr as a terminating error. The follow-up commit `98a6695` confined `Continue` to captured native execution, restored the original preference and explicitly checked `$LASTEXITCODE`. The next lab execution progressed through lint, type checking and fake-VISA tests, supporting this first correction.

That second attempt, at `98a6695`, failed later in copied-INI preparation with `SyntaxError: '(' was never closed` at `raise SystemExit(Missing`. Its inline multiline Python `-c` code actually contained `raise SystemExit("Missing [Instruments]")`. Loss of quotes at the Windows PowerShell/native `uv`/`python` command-line boundary is the leading explanation; the log alone does **not** identify conclusively which native process boundary discarded them. No instrument was accessed in either failed attempt.

The replacement eliminates the vulnerable command-line path rather than changing quote escaping:

- `tools.edfa_prepare_readonly_config` accepts ordinary `--source`, `--destination` and `--log-file` path arguments. It requires exactly one Instruments section, a physical backend and `GPIB0::12::INSTR`.
- It forces `edfa_m4_write_enabled = false`, redirects the GUI diagnostic log into the evidence directory and validates the complete resulting INI through the application's typed configuration model and matching parsing rules.
- Only **after** successful validation does it atomically publish a temporary write-disabled file. The source is never written, an existing destination is rejected, and validation failures leave no final destination.
- The PowerShell report sets `offline = PASS` only after lint, formatting, Pyright, fake-VISA tests, safe INI creation and a no-VISA dry run have all completed.
- Untracked files in a reused checkout now cause rejection; existing clean pinned detached worktrees are reusable without destructive deletion.

Hardware-independent tests cover original-file preservation, preexisting destinations, write-enabled source INIs, malformed/missing files, malformed sections, wrong backend/address, typed-validation failures, Unicode and space-containing paths, log redirection, and the CLI's exit codes. The Windows-only test runs the CLI through a **real Windows PowerShell 5.1 process**. A separate GitHub Actions Windows job runs the **entire Offline acceptance script**, with local-only synthetic Git origin, isolated worktree, synthetic INI, actual `uv` invocations, report/evidence assertions and negative exit-code/report checks. Its outcomes must be checked at the exact final commit SHA.

A Windows CI PASS validates that CI's Windows environment and **synthetic input** exercised the complete Offline path. It does not establish successful execution on the laboratory PC, does not exercise real GPIB, and does not authorize read-only physical communication or M4 writes. The next lab rerun must be `-Stage Offline` only, using a newly reviewed SHA.

## Next supervised read-only experiments: isolated hypotheses

Keep each physical test a separate operator-approved session with verified optical isolation, independently established emission state, exclusive GPIB ownership, and no setters, clear/reset, automatic retry, or startup connection. Stop at the first error.

| Test | Diagnostic purpose | Interpretation of evidence (not proof of cause) |
| --- | --- | --- |
| A: 1 session, 0 ms pacing, 16-query plan | Baseline reproducibility under a single VISA lifetime | A first-write listener failure suggests address/bus/device readiness; a late failure suggests state/sequence dependence, but does not isolate hardware |
| B: 2 sequential sessions, 0 ms pacing | Test sensitivity to closing and reopening a VISA resource | Failures following reopen strengthen session/controller/listener-transition hypotheses; equal failures in both weaken a purely reopen-specific explanation |
| C: 1 session, 50 ms pacing | Test whether command cadence affects observed listener failures | Fewer failures are consistent with timing sensitivity but do not prove a required timing interval; a failure despite pacing weakens simple cadence explanations |
| D: separately supervised NI-MAX / I/O Trace read-only observations | Compare Python-independent controller behavior and capture native timestamps/handshakes | A similar failure outside Python weakens application-only explanations; consistent NI-MAX success narrows differences but cannot exclude intermittent cabling |
| E: inspect controller model, bus topology, cables, NI-VISA/488.2 and firmware | Identify physical and configuration confounders | Correlation with a single cable/controller/device or competing bus owner increases its priority for controlled follow-up |

Do **not** automatically repeat tests until an error has occurred: record the timestamp, last successful query, failed `viWrite` or `viRead`, reported signed/hex status, active controller and VISA session, other attached GPIB devices, and environmental/operator conditions.
