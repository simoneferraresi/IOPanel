# M3: IPG EDFA read-only GUI

IOPanel now contains a fifth tab, **IPG EDFA**, after **Thorlabs PM5020**.
It uses the same broad composition as the PM5020 tab: a **compact 320–400 px
control column on the left** and an expanding **telemetry dashboard on the
right**. The dashboard groups optical power, amplifier status and observed
alarms. Values use the application's existing Geist typography, and the
page contains **no scroll area or scrollbars**; available space is shared
by the native Qt layouts.
The default backend is **simulation**. Constructing the tab never opens a
VISA resource, imports the native VISA provider, scans GPIB addresses,
starts the EDFA worker, or sends commands. No GUI controls can enable
emission or change gain, diode current, optical power or mode.

The tab provides explicit Connect/Disconnect, Read once, and Start/Stop
monitoring (2/5/10-second choices). All I/O is serialized in a dedicated Qt
worker thread, which owns the amplifier backend and its timer. Changing tabs
does not start monitoring. Application shutdown asks this worker to close its
session and waits for the thread to finish; it never force-terminates a native
instrument operation.

Physical mode requires operator selection in the ordinary, validated
[Instruments] INI settings, a unique configured GPIB resource, and an
explicit Yes confirmation before *every* physical connection. The separate
site-local configuration established for M2 can be used when deliberately
starting the GUI with:

```powershell
uv run --locked python app.py --config config.lab.ini
```

Never change the shared `config.ini` merely to test a physical amplifier,
and never commit the local lab configuration. Connecting may
switch GPIB remote/local mode. The operator must ensure emission is off,
the setup is approved and no other program owns the GPIB controller.
No automatic fallback from physical failure to simulation occurs. The tab
cannot orchestrate a CT400 scan or PM5020 acquisition.

The telemetry table always distinguishes reported numerical optical power
from the nonnumerical instrument response "Off"; the setpoint is not measured
power and built-in input monitoring is only indicative. Status bits are
observations, not a safety interlock. An extra advisory reports when input
monitoring is below the amplifier's own minimum even if the alarm bit is clear.

## Laboratory context

The supervised 2026-10-20 read-only EDFA snapshot came from
GPIB0::12::INSTR, firmware Rev 1.1.175. The CT400 was off, the EDFA was
not connected to an optical input or output path, and the observed low
input monitor value (0.06 mW versus the amplifier-reported 0.23 mW minimum)
was **expected for this disconnected setup**. The absence of the low-input
status bit under these conditions does *not* qualify its behavior during
active emission. No optical-emission tests have been performed.

**Only offline simulator GUI behavior is included in M3 automated tests.**
Before using the new physical GUI on the lab computer, supervise a separate
read-only Connect/Read once/Disconnect test. Check firmware identification,
numeric units, raw status and graceful shutdown under both successful and
failed VISA operations. Do not connect the CT400 or enable EDFA emission as
a substitute for this separate GUI-readout acceptance gate.

Future milestones may add graphs, logging/export and coordinated nonlinear
experiments, but active optical control requires its own reviewed safety
interlocks and physically verified seed-loss behavior.
