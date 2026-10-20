# M2: read-only GPIB/VISA transport (offline code; physical qualification pending)

## Boundaries and intended deployment

The lab's IPG EAD-2K-C is connected by GPIB. The **only** supported M2
resource grammar is an explicit GPIB primary address of the form
`GPIB0::10::INSTR` (example only; discover the actual address independently
from the amplifier front panel). No automatic scan of addresses or fallback
to other instruments is allowed.

`IPGEAD2KCGPIB` does not connect in its constructor. The explicit
`connect()` lazily obtains a PyVISA ResourceManager, opens exactly the configured
resource, sets a finite VISA timeout, then sends only `*IDN?`. Identity must
contain `EAD-2K-C` before telemetry queries can proceed. The real `*IDN?`
response format is not yet known; if the unit reports a different form,
connection is rejected and must be documented/qualified rather than bypassed.

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

GPIB EOI/read termination, VISA timeouts, query return framing, actual
status-number syntax, physical identity and remote/local behavior must be
measured in a separately authorized read-only bench gate before enabling
IOPanel physical use. No physical instrument has been operated for M2.

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
