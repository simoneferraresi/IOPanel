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
