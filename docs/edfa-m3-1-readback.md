# M3.1 — IPG EAD-2K-C extended read-only configuration (software qualification)

## Evidence, scope, and safety

**Model:** IPG EAD-2K-C; physical unit reported `IPG EAD-2K-C, Rev 1.1.175`.
**Source:** earlier operator-provided photographs of IPG P21-006000 Rev A,
pp. 27–32 (protocol), and P21-008000 Rev A, pp. 33–34 (specifications).
The original photographs have now been rechecked directly in the operator-provided
`EDFA manual.zip` archive: `IMG_5264.jpeg` (GPIB command table p.31),
`IMG_5265.jpeg` (p.32), `IMG_5298.jpeg` (status bits p.29), and
`IMG_5266.jpeg` (optical specifications p.33). All five queries, expected
readback meanings and units match this model's manual. The photographs are
**not committed to the repository**. Public EAD-family brochures and manuals for
*other* models are not substitutes for this command table.

M3.1 adds five narrowly allowlisted, **non-setting** GPIB readbacks.
No emission control, power/gain/current writes, reset, event clearing,
arbitrary SCPI, bus enumeration, auto-connect or automatic new-command polling
is introduced. Physical connection remains operator gated and may change the
VISA/GPIB remote/local state. **All five new commands returned valid numeric readbacks on the laboratory unit (2026-11-03); GUI readback was also observed.**

### Command-coverage inventory

Evidence levels:
- **Manual-review:** earlier review of the photographed P21-006000 Rev A
  pp. 29, 31–32; originals unavailable in the repository.
- **Physical:** successful EAD-2K-C telemetry at GPIB0::12::INSTR
  on 2026-10-20 and 2026-11-03, with firmware Rev 1.1.175.
- **Offline:** fake query/VISA sessions and simulator tests, *not* real hardware.

| Exact GPIB query | Readback | Response / units | Capability and side effects | Implementation / qualification |
| --- | --- | --- | --- | --- |
| `*IDN?` | Identity | ASCII string | GPIB session may change remote/local state | M1–M3, physical |
| `SOUR:POW:STAT?` | Status word | unsigned decimal 32-bit integer | Mode and capability bits; sequential state can change | M1–M3, physical |
| `INP?` | Input optical power | finite float, mW or dBm (bit 18) | Only query when bit 21 set | M1–M3, physical |
| `INP:LIM:LOW?` | Minimum input threshold | finite float, mW or dBm (bit 18) | Only query when bit 21 set | M1–M3, physical |
| `SOUR:POW?` | Measured output power | W or dBm, or literal `Off` | `Off` means no numeric measurement, not zero or safety | M1–M3, physical |
| `SOUR:POW:SPO?` | Programmed output-power setpoint | W or dBm (bit 18) | Not measured power; may be inactive in other modes | M1–M3, physical |
| `SOUR:POW:BACK?` | Reflected power monitor | percent of device maximum | Only query when bit 22 set; bit clear on lab unit | M1–M3, offline; physical query skipped |
| `SOUR:CURR?` | Measured pump-diode current | amperes | Not programmed current | M1–M3, physical |
| `SOUR:TEMP:LEV?` | Case temperature | °C | Not a safety interlock | M1–M3, physical |
| `SOUR:POW:LIM:HIGH?` | Hardware-reported output-power limit | W or dBm (bit 18) | Model-specific command; must qualify firmware reply | **M3.1 manual-confirmed and physical readback verified (2026-11-03)** |
| `SOUR:CURR:SPO?` | Programmed diode-current setpoint | amperes | Not measured diode current | **M3.1 manual-confirmed and physical readback verified (2026-11-03)** |
| `SOUR:CURR:LIM:HIGH?` | Hardware-reported diode-current limit | amperes | Not a universal model constant | **M3.1 manual-confirmed and physical readback verified (2026-11-03)** |
| `SOUR:GAIN:SPO?` | Programmed gain setpoint | dB | Skipped unless AGC-available bit 20 set; mode-dependent meaning unqualified | **M3.1 manual-confirmed and physical readback verified (2026-11-03)** |
| `SOUR:GAIN:LIM:HIGH?` | Hardware-reported gain limit | dB | Skipped unless AGC-available bit 20 set; mode-dependent meaning unqualified | **M3.1 manual-confirmed and physical readback verified (2026-11-03)** |

The raw response is stored for each successful additional readback. No
generic fallback command forms are attempted. A response of `Off` for any
*new* numeric query is **not** accepted: only `SOUR:POW?` has physically
demonstrated that special response.

**Coverage denominator:** 14 commands in the *currently identified,
non-mutating EAD-2K-C query inventory*, of which 14 have software
implementations and **13 have been exercised physically**
(the back-reflection query was skipped because its capability bit was clear).
This is **not** a claim that the complete manufacturer's SCPI command table
contains only 14 queries; exact total manual coverage cannot be measured
without rechecking the full original manual. Of the five *new* readbacks:
5/5 successfully tested individually and together on firmware Rev 1.1.175,
and 5/5 covered by offline regression tests. Tests covered the observed
emission-off APC state, not every possible operating mode.

### Explicitly deferred standard/event commands

| Candidate | Status | Reason |
| --- | --- | --- |
| `*STB?` | Not allowlisted | EAD-2K-C-specific support and status-byte semantics unverified |
| `*ESE?` | Not allowlisted | EAD-2K-C-specific support unverified |
| `*OPC?` | Not allowlisted | Potential blocking/timeout semantics |
| `*ESR?` | Not allowlisted | Read-and-clear event-register side effect |
| `*CLS`, `*RST`, setters and emission commands | Prohibited | Mutate state, acknowledge/clear events, or control optical operation |

### Software architecture

- `GPIBReadOnlyReader`: fixed allowlist, strict finite numeric decoding,
  raw ASCII preservation, no write methods. `read_configuration()` queries
  one status word and the five new commands (only three when AGC not available).
  `read_qualification_value()` restricts one-off manual qualification to
  those five exact queries.
- `EDFAConfiguration`: immutable, slot-based, UTC timestamped snapshot
  independent of the frequently updated `EDFATelemetry`. It preserves
  explicit power units, separate measured/programmed/maximum current
  fields, gain in dB, raw response pairs and `None` for missing AGC gain
  capability. Unexpected numeric response and communication failures are
  **errors**, not `None`.
- `IPGEAD2KCGPIB`: owns and serializes the only VISA session. Both
  configuration and individual qualification queries recheck status bits
  for mode, units and availability after sequential acquisition.
  Inconsistent status, timeout, unsupported response or malformed data
  enters ERROR and requires close/reconnect. The normal telemetry sequence
  and its existing status recheck remain unchanged.
- `DummyEDFA`: deterministic explicit values, AGC-dependent gain readbacks
  and a separately injectable `read_configuration` failure. Simulation
  does not prove real firmware behavior.
- `EDFAMonitorWorker`: configuration reads are queued and serialized in
  the same owner thread; requests during periodic monitoring are rejected.
- `EDFAReadOnlyPanel`: compact control column, existing optical/status
  dashboard, additional read-only configuration section. Values show
  **Settings read at (UTC)** and are not automatically refreshed. A manual
  refresh button is disabled while monitoring. In physical mode it requires
  an additional Yes confirmation. Disconnect clears the cached readback;
  detected mode/unit/AGC capability changes invalidate stale values.

If the hardware firmware rejects a readback, do not automatically substitute
a different query string or fake a zero. No "unsupported" result is inferred
merely from a timeout; investigate under a separate approval.

### M3.1 laboratory and GUI acceptance evidence — 2026-11-03

On the laboratory PC, the operator ran the standalone read-only probe
using `GPIB0::12::INSTR`. The eight submitted logs comprised a dry run,
a baseline, five individual new-query sessions and a combined configuration
snapshot. Every physical session identified `IPG EAD-2K-C, Rev 1.1.175`
and returned status word `3145729` (APC active, AGC available,
input-power monitor available, linear power units). No failures or status
inconsistencies were reported.

| Query | Individual raw response | Meaning confirmed from manual | Combined readback |
| --- | --- | --- | --- |
| `SOUR:CURR:SPO?` | `0.700` | Current setpoint, 0.700 A | 0.700 A |
| `SOUR:CURR:LIM:HIGH?` | `2.400` | Maximum current, 2.400 A | 2.400 A |
| `SOUR:POW:LIM:HIGH?` | `2.200` | Maximum output-power setting, 2.200 W | 2.200 W |
| `SOUR:GAIN:SPO?` | `37.0` | Gain setpoint, 37.0 dB | 37.0 dB |
| `SOUR:GAIN:LIM:HIGH?` | `38.0` | Maximum gain, 38.0 dB | 38.0 dB |

Combined capture time: `2026-11-03T10:32:11.120260+00:00`.
The operator subsequently exercised the new GUI manual-refresh panel and
provided a screenshot timestamped `2026-11-03 10:38:04 UTC`. Its five
displayed values match the independent combined readback exactly; the
manual refresh and timestamp display are therefore observed to work on the
physical amplifier. The operator also reported normal GUI behavior.

**Important boundaries:** The control mode remained APC, so a readable
gain setpoint does not imply AGC is active. In these seven physical
sessions, `SOUR:POW?` returned `Off` (not a measured zero);
the reported output-power setpoint remained 0.20 W, current monitor
0.021 A and case temperature 23.4 °C. Input monitor values of
0.05–0.11 mW were below the reported 0.23 mW input threshold, despite
no active low-input status bit. This is not evidence of optical safety
or permission to enable emission. The reported 2.2 W limit is a
configuration readback, not proof that such power is being produced.
The standalone JSON's `physically_qualified: false` and
`UNQUALIFIED` note are unchanged static software caveats and do not
reflect a failed hardware response.

Independent evidence for disconnect clearing, all simulated error
recoveries, resource cleanup/remote-local effects and behavior in other
control modes was not supplied; offline tests cover the software paths.
The manual photographs and laboratory logs remain operator-supplied,
not stored in Git.

### Historical physical reference — 2026-11-03

An operator-supervised **baseline-only** read-only probe succeeded using
`uv run --locked python -m tools.edfa_readonly_probe --config config.ini --connect-readonly --operator-confirmed`
on the laboratory PC `C:\Users\bellanca\Desktop\IOPanel`, resource
`GPIB0::12::INSTR`. Identity: `IPG EAD-2K-C, Rev 1.1.175`;
status `3145729` (APC; bits 20 and 21 set);
`SOUR:POW?` returned `Off`, input monitor 0.08 mW, reported input
minimum 0.23 mW, power setpoint 0.20 W, diode current 0.021 A and
case temperature 22.7 °C. This **does not** qualify any new M3.1 query.
The observed input below its reported minimum with no low-input alarm does
not prove safe seed or emission conditions. Do not enable emission.

The earlier 2026-10-20 baseline observation remains recorded in
`docs/edfa-gpib-readonly-transport.md`.

## Operator-gated M3.1 qualification (laboratory PC only)

1. Inspect the **original printed manual** again, especially the five exact
   commands and each return unit/format; compare the installed firmware.
   Do not query a candidate if its safety or syntax is uncertain.
2. Ensure the optical path is approved, emission remains OFF, no other
   program owns GPIB, and the configured primary address is **12**.
   Use an uncommitted local `config.lab.ini` with
   `edfa_backend = physical` and `edfa_gpib_resource_name = GPIB0::12::INSTR`.
   No seed or emission is required for passive readback, but that does not
   authorize any active operation.
3. Confirm the dry run opens no VISA session:

   ```powershell
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini
   ```

4. Recheck the previously qualified baseline:

   ```powershell
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed
   ```

5. With **separate supervision for each run**, use **one additional query
   at a time**, beginning with current/power limits and deferring gain
   commands until AGC availability is confirmed:

   ```powershell
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed --qualification-query "SOUR:CURR:SPO?"
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed --qualification-query "SOUR:CURR:LIM:HIGH?"
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed --qualification-query "SOUR:POW:LIM:HIGH?"
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed --qualification-query "SOUR:GAIN:SPO?"
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed --qualification-query "SOUR:GAIN:LIM:HIGH?"
   ```

6. For every command, archive operator notes, identity, status, exact raw
   reply, parsed number, unit, result/error, firmware revision and resource
   cleanup outcome. Any timeout, unexpected reply or status transition is a
   **stop gate**: no automatic retry or mode/power change. Verify manual
   semantics before classifying a response as legitimately unavailable.
7. Only after **all supported commands** have passed individual qualification
   may the operator invoke the full snapshot:

   ```powershell
   uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed --extended-configuration
   ```

8. Then separately qualify the GUI manual refresh after Connect,
   Read once and Disconnect; verify no periodic extended readbacks, no
   UI blocking, and proper resource release at shutdown. Monitor for stale
   configuration timestamp and correct unknown/unavailable labels.

Never use these commands to diagnose missing optical output by switching
emission on. If resource release cannot be confirmed, do not reconnect
until the operator restores and verifies the instrument state.

## Deferred versus implemented in M4

M4-A now adds **site-gated, operator-confirmed** power/gain/current setpoint and power-unit writes in a separate control path. The five M3.1 readback queries were physically verified on 2026-11-03; their earlier software-only warnings are obsolete. No physical M4 writes have yet been qualified. Mode selection and physical emission;
seed-loss/interlock qualification; and stateful control safety remain
**outside M3.1**. Avoid adding setters to the read-only abstraction.
