# M2: read-only GPIB/VISA transport (first physical telemetry verified; broader qualification pending)

## Boundaries and intended deployment

The lab's IPG EAD-2K-C is connected by GPIB. The **only** supported M2
resource grammar is an explicit GPIB primary address of the form
`GPIB0::10::INSTR` (example only; discover the actual address independently
from the amplifier front panel). No automatic scan of addresses or fallback
to other instruments is allowed.

`IPGEAD2KCGPIB` does not connect in its constructor. The explicit
`connect()` lazily obtains a PyVISA ResourceManager, opens exactly the configured
resource, sets a finite VISA timeout, then sends only `*IDN?`. Identity must
contain `EAD-2K-C` before telemetry queries can proceed. One real amplifier
responded `IPG EAD-2K-C, Rev 1.1.175` on 2026-10-20; unexpected future
identity responses must be investigated rather than bypassing this guard.

The backend itself never sends emission commands, resets, setpoints, mode
changes, clears, error resets, or changes to power-reporting units. It does
not modify `config.ini` or GUI startup. The underlying VISA session can
still trigger remote/local control side effects: even connecting and querying
requires a supervised operator-approved physical gate.

Only read-only telemetry queries defined in the M1 protocol allowlist are
sent. All calls are synchronous and lock-serialized in this backend; any
future Qt UI must use a worker thread (never the GUI thread). Native errors
or invalid replies move the backend to ERROR until an explicit close and
reconnect. Closing attempts to release both resource and manager, retains
unreleased handles on failures, and does not pretend that optical emission
was disabled.

## Dependency and packaging

PyVISA 1.15.0 is pinned in the core project dependencies because it is
pure Python, has negligible startup impact with the current lazy import, and
must be bundled by the Windows one-folder build, which excludes the lab
dependency group. `uv.lock` is resolved with the pinned project uv release
and verified in CI. PyVISA itself is **not** an IEEE-488 controller driver:
the installed host VISA runtime and controller remain external dependencies.
The PyInstaller spec explicitly retains PyVISA despite lazy import.

No alternative native VISA runtime is installed or selected automatically.
Physical qualification must still establish controller identity, GPIB address,
remote/local effects, termination, EOI, and communication behavior.

On the lab PC, inspect the local VISA provider, IEEE-488 controller, intended
resource and amplifier's physical GPIB address without triggering instrument
writes or generic bus probing. No unit serial or manufacturer identification
is inferred from a configured resource alone.

## First physical read-only observation: nonnumeric output power

During the initial operator-supervised GPIB test of the IPG EAD-2K-C
on Windows 10, with NI VISA, GPIB primary address **12**, the device
identified successfully and returned a parseable decimal status. The
`SOUR:POW?` query returned the literal text `Off` while optical
emission was disabled. The original numeric-only parser therefore
rejected the response. This is physical evidence of that **single
query's reply**, not proof of the state or behavior of other queries.

The read-only codec now recognizes exactly `Off` (case-insensitively)
**only for `SOUR:POW?`**, stores
`output_power=None` and
`output_power_non_numeric_reply="Off"`, and still rejects unexpected
nonnumeric responses elsewhere. A reported `Off` is **not 0 W** and
does not prove optical safety or a verified safe emission state. The
commissioning JSON reports both the missing numerical power and the
literal instrument reply.

### First completed full read-only round trip — 2026-10-20

On Windows 10 with CPython 3.12.8, PyVISA 1.15.0 and the detected NI VISA
provider, an operator-supervised probe completed at `GPIB0::12::INSTR`
with all queries and connection cleanup returning without an exception.
The amplifier identified as `IPG EAD-2K-C, Rev 1.1.175`.

Captured read-only snapshot at `2026-10-20T13:45:50.794199+00:00`:

| Field | Observed value | Interpretation |
| --- | --- | --- |
| Raw status | `3145729` (`0x00300001`) | Bits 0, 20 and 21 set |
| Mode | APC | Active mode bit 0 |
| AGC available | Yes | Capability bit 20, not active AGC mode |
| Input monitor available | Yes | Capability bit 21 |
| Back-reflection monitor | Not advertised | Bit 22 clear, so value absent |
| Output-power response | `Off` | No numeric output reading; **not** 0 W |
| Input power | 0.06 mW | Indicative built-in optical monitor |
| Minimum input-power threshold | 0.23 mW | Device-reported threshold |
| Power setpoint | 0.20 W | Configured setting, **not measured output** |
| Diode current | 0.021 A | Read-only current observation |
| Case temperature | 25.8 °C | Read-only temperature observation |
| Interlock/overtemperature/low-input/reflection alarm bits | All clear | Must not be equated with optical safety |

**Important discrepancy:** the input monitor reads 0.06 mW, below the
device-reported 0.23 mW threshold, despite status bit 4 ('input power
low') being clear. Its meaning may be conditional on an emission state,
but this has **not** been established on hardware. Do not use the
absence of this flag to authorize emission or override the documented
input operating range (-6 to +10 dBm). **Keep emission OFF** and obtain
an independently verified adequate seed and approved optical setup before
a separately authorized active-emission milestone.

An earlier physical attempt terminated with a generic
`EDFACommunicationError`; its underlying cause was not captured.
A subsequent read-only probe returned normally. Consequently, this
documents **one successful complete session**, not proven long-run
communications reliability. Controller-specific EOI/termination behavior
beyond the observed success, GPIB remote/local effects, repeatability
and fault recovery still require qualification. No emission or setpoint
commands were sent.

Do not enable emission simply to obtain a numeric reply.

## Query consistency and limitations

Before telemetry the driver checks `*IDN?` for the expected model token.
`GPIBReadOnlyReader.read_telemetry()` reads status and then sequentially
queries power, setpoint, current, temperature and available monitors. A
second status query checks the power-unit and monitor-availability flags.
If they disagree, the driver discards the result and requires recovery.

Matching endpoint status words still do **not** establish that every
reading was acquired simultaneously or that the status could not have
transiently changed and reverted. No emission state or safe optical
condition can be inferred from connection success.

A single physical read-only run established the observed identity, decimal
status syntax and responses under the configured transport. It did **not**
verify remote/local effects, robustness over repeated sessions, or fault
recovery. GPIB EOI/read termination behavior and any future changes to
firmware/response framing must remain visible qualification requirements.
The separate GUI integration and any emission control are out of scope.

RS-232 remains a later interface; no code is added for it here.


## Explicit configuration and supervised probe

The original INI remains backward-compatible. New optional fields in
`[Instruments]` default to simulation, empty resource and conservative 3 s timeout:

```ini
edfa_backend = simulation
edfa_gpib_resource_name =
edfa_gpib_timeout_ms = 3000
edfa_gpib_write_termination = none
edfa_gpib_read_termination = none
```

`none` means no extra ASCII termination is appended or stripped by PyVISA;
GPIB EOI behavior is provided by the installed VISA/controller. The optional
alternatives `lf`, `cr`, and `crlf` must only be selected after verifying the
manufacturer's response framing on this physical system. Setting these options
does not send any bus commands.

Use this read-only, standalone dry-run command without opening IOPanel:

```powershell
uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini
```

After an **approved operator-supervised session**, configure the physically
verified unique GPIB address and `edfa_backend = physical` in a local
non-committed INI, check all amplifier safety/optical conditions, and run:

```powershell
uv run --locked python -m tools.edfa_readonly_probe --config config.lab.ini --connect-readonly --operator-confirmed
```

The helper opens precisely one configured session; it first checks
`*IDN?`, then makes only the previously allowlisted non-mutating queries
and prints the raw status and telemetry. There is no automatic resource
enumeration, no auto-emission, no EDFA integration in normal GUI startup and
no optical shutdown control. The helper attempts session cleanup even on
query failure. If the real identity text does not contain `EAD-2K-C`,
the device is rejected; do not bypass the guard without a separate review.
Do not use this helper while another program owns the same GPIB controller.

A missing or failing VISA provider, unrecognized device, timeout, unexpected
response, missing seed/interlock, or uncertain connection state is a failed
qualification gate, **not** permission to retry with emission enabled.

Only after physical evidence supports it should a later milestone introduce
any interactive or continuous EDFA monitoring. M2 has no active optical
control and no GUI connection.
