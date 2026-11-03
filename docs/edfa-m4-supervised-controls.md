# M4 — Supervised IPG EAD-2K-C controls: command coverage, evidence and acceptance

**Scope:** M4-A physical setpoint and reporting-unit commands behind explicit site-local
authorization; offline simulator of setters, state transitions and emission. Firmware
reference: **IPG EAD-2K-C, Rev 1.1.175**. Manufacturer authority: the operator-provided
photographs of **IPG P21-006000 Rev A**, notably
`IMG_5264.jpeg` (p.31), `IMG_5265.jpeg` (p.32),
`IMG_5298.jpeg` (p.29), all in `EDFA manual.zip`.
These photographs are **not checked into GitHub**.

## Evidence and safety boundaries

The five M3.1 extended readbacks were physically verified on **2026-11-03**
at GPIB0::12::INSTR: current SP 0.700 A, current maximum 2.400 A,
power maximum 2.200 W, gain SP 37.0 dB, gain maximum 38.0 dB.
Firmware Rev 1.1.175. This qualifies *readback*, **not a single write**.

In those tests, status 3145729 meant APC selected, AGC capability present,
linear optical-power units. `SOUR:POW?` responded `Off` and the
reported input 0.05–0.11 mW was below the reported 0.23 mW threshold.
Neither the output token nor alarm bits independently prove safe conditions.

All M4 operations in automated tests use simulated resources or fake VISA
sessions. **No physical parameter writes or emission tests are claimed.**

The read-only allowlist remains separate. Physical VISA session creation,
read/write serialization and cleanup remain inside `IPGEAD2KCGPIB`,
owned by the existing `EDFAMonitorWorker` Qt thread. No separate VISA
owner, hardware discovery, arbitrary SCPI field, automatic connection, timer
writer, rollback or automatic retry has been added.

### Exact write-command inventory

| Command | Manual | Argument/units | Active mode and preconditions | Acknowledgment and verification | Physical qualification |
| --- | --- | --- | --- | --- | --- |
| `SOUR:POW <number>` | p.32 | Finite decimal W or dBm, **same as current device unit** | APC; fresh hardware `SOUR:POW:LIM:HIGH?`; approved independent lab ceiling in W; confirmed emission OFF | `write()` has no normal response; requery `SOUR:POW:SPO?` plus status, configuration, telemetry; 0.0005 W or 0.1 dBm absolute tolerance | **Unqualified** |
| `SOUR:GAIN <number>` | p.32 | Finite decimal dB | AGC active **and** advertised available; fresh gain maximum; approved lab ceiling | `write()` then `SOUR:GAIN:SPO?`; 0.05 dB absolute tolerance | **Unqualified** |
| `SOUR:CURR <number>` | p.31 | Finite decimal A | ACC active; fresh current maximum; approved lab ceiling | `write()` then `SOUR:CURR:SPO?`; 0.0005 A absolute tolerance | **Unqualified** |
| `UNIT:POW W` or `UNIT:POW DBm` | p.32 | Enumerated exact tokens only | Independently confirmed emission OFF; same site-local M4 opt-in; no implicit GUI preference write | `write()` then fresh status and all power-bearing telemetry/configuration readbacks (device unit flag bit 18); exact enum verification | **Unqualified** |
| `SOUR:POW:STAT 0` | p.32 | Literal OFF | Simulator only; physical state verification and emergency procedure unqualified | Typed `EDFAEmissionAction.OFF`; simulator changes state | **Not physically enabled** |
| `SOUR:POW:STAT 1` | p.32 | Literal ON | Simulator only; seed, routing, key, interlocks, protection, failure semantics NOT qualified | Typed `EDFAEmissionAction.ON`; simulator changes state | **Physically prohibited** |

No lower manufacturer bound is invented for gain. Linear negative power/current
is rejected as physically invalid, and all values must be finite. dBm power
is converted to W **only for the independent laboratory ceiling comparison**;
this never changes the device units. The hardware maximum is not the laboratory
ceiling. Values are formatted with nine significant digits, and readback
comparisons have zero relative tolerance and the conservative absolute
tolerances listed above. These are initial **software verification**
tolerances, not manufacturer resolution specifications; physical qualification
may require revisiting them without relaxing safety limits.

### Status/readback gates and failure semantics

Each attempted write (even if a GUI readback is visible) performs new
telemetry, configuration, and status queries, compares the full sampled status
words and checks the active mode, capability, unit, and device maximum.
It refuses observed transient emission-starting/3-second-startup bits,
known fault flags, reserved/unassigned status bits, incompatible mode,
wrong unit or missing readbacks. Physical transactions additionally require
the `SOUR:POW?` response `Off`, **plus a separate per-operation operator
attestation** that emission is physically OFF. Neither observation replaces
physical verification of key-switch, interlocks and optical routing.

A failed software precondition produces REJECTED, sending **no write**.
A request cancelled before the command is sent produces CANCELLED. Exactly
one allowlisted `session.write(command)` occurs per permitted transaction;
there is no ordinary command response. If the write raises an exception,
the outcome is UNKNOWN even if the command might have reached the instrument.
An unparseable/failed post-write readback is UNKNOWN; a numeric mismatch
is MISMATCH. Both require physical verification and explicit recovery.
The backend enters ERROR and cannot execute more writes before
disconnect/reconnection. A successful VISA `close()` **does not guarantee
optical shutdown**. A status flag indicating no fault does not certify safety.

The Qt request contains a thread-safe cancellation event. Disconnect/shutdown
sets that event before queuing cleanup, preventing an **unsent queued**
transaction from later applying. A command already handed to the VISA driver
cannot be recalled; the GUI must report uncertainty rather than assert
cancellation stopped physical activity. Work is never deliberately moved
to a second resource owner.

### Site-local operator opt-in

Default: `edfa_m4_write_enabled = false` (physical controls unavailable).
Only an **uncommitted, independently approved** local INI may enable them.
All of the following must be reviewed, completed and valid in `[Instruments]`:

```ini
# ILLUSTRATION ONLY — these values are intentionally not an approval.
# NEVER copy unspecified ceilings or authorization from the manual.
edfa_m4_write_enabled = false
edfa_m4_site_reference =
# edfa_m4_approved_power_w =
# edfa_m4_approved_gain_db =
# edfa_m4_approved_current_a =
```

Enabling writes requires a site reference (8–80 letters/numbers/._-)
and **three explicitly supplied positive finite ceilings** in W, dB and A.
The reference is an audit label, not a cryptographic authorization.
These three ceilings are deliberately absent from default configuration.
This source change does not edit the lab computer's INI. The GUI also
requires separate Yes confirmation for each operation, explicitly
attesting independent emission-off checks for physical operations.
An INI change by itself is not proof that optical conditions are safe.

The default simulator can exercise requests offline with its own
simulated ceilings. It does not grant physical approval. The GUI
labels pending edits separately from device readbacks. Editing fields,
changing tab, connecting, selecting the unit combobox and starting
monitoring never send a setpoint command. A *separate* confirmed
"Change" action uses `UNIT:POW` and refreshes affected values.

### Not included in ordinary M4-A settings

`*ESE`/`*OPC` are instrument-management/event operations;
`*CLS`, `*RST` and `SYST:ERR:RES` clear or reset instrument state.
They are **not** included in the setter allowlist or GUI.
The RS-232 `APC`, `AGC`, `ACC` mode-selection command names have no
documented corresponding GPIB setters in these photographs. Active mode
is **read-only** over GPIB. No inferred `SOUR:MODE` commands exist.

## Offline test evidence

`tests/test_edfa_supervised_controls.py` and
`tests/test_edfa_m4_panel.py` cover command tokens, finite values,
authorization and limits, correct mode, fresh status, single writes,
readback comparison, cancellation, injected failures, mode and unit change,
Qt confirmation and worker serialization, and simulation-only emission.
Retained M1–M3.1 test files exercise the query-only path. The complete
suite, Ruff, Pyright and Windows packaging must be checked by CI on the
draft PR. These tests **cannot** qualify hardware safety or firmware
setpoint behavior.

## Staged supervised laboratory acceptance — NOT authorized by this PR

1. Review code and green Windows CI; freeze the PR for independent safety
   review. Confirm mode, units, exact firmware, hardware and local ceilings.
2. On the lab PC, repeat only the **already qualified read-only** CLI probes
   and GUI readbacks. Verify all five M3.1 values, fresh timestamps,
   read-only behavior, no automatic writes, disconnect and recovery.
3. Independently approve a physical optical setup with the laser
   safety officer/principal operator: optical path and beam dump,
   compatible input seed, isolation and routing, maximum allowed power,
   attenuators, power measurement, key switch, interlock, accessible stop
   procedure, and safe response to seed loss or link loss. Record the
   authorization and three measured lab ceilings. A status bit alone
   does not substitute for these checks.
4. Obtain **fresh, explicit authorization for each proposed physical
   write**. First test a conservative, no-emission setpoint change in the
   active APC mode (not mode switching); compare independent physical
   panel/state with before/after GPIB responses. Verify a no-op
   failure path, cancellation and disconnect separately **without**
   risking an energized setup. Record command, raw readback, units,
   identity, status, errors and operator signature.
5. Only after independent acceptance of the APC write may any
   `UNIT:POW` write be considered. Check actual device unit changes
   against independent instrument readouts and convert safety ceilings
   correctly; demonstrate no implicit unit writes.
6. AGC and ACC setters require a **separate manufacturer-supported
   mode-selection procedure and qualification**; they remain inaccessible
   to a physical GPIB unit in APC mode. Do not invent a mode-changing
   command merely to exercise a setter.
7. Physical emission ON is **a distinct M4-B gate**, not a continuation
   of successful reads or setpoint writes. Verify seed adequacy, optical
   routing, interlocks, optical output limits, and full failure handling
   under a separate operator-approved plan. No physical ON command is
   sent by this implementation; emission OFF is simulator-only until
   a reliable physical shutdown and verification procedure is established.

**No lab authorization, physical write, optical shutdown, optical output
verification or packaging success is inferred from this document alone.**
