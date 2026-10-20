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

## Local dependency and packaging gap

The M2 draft intentionally does **not** modify the project's locked
`uv.lock` or `pyproject.toml` dependencies to add PyVISA. The new module
imports `pyvisa` only inside an explicitly requested physical connection;
all unit tests run with injected fake VISA sessions.

**Physical commissioning is blocked** until maintainers add a pinned,
resolved PyVISA dependency through the normal `uv lock` process, validate
the installed VISA runtime/controller, and qualify packaging. Do not work
around that gate by silently installing a different/second native VISA
runtime or using `--no-deps` to bypass the lock.

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
