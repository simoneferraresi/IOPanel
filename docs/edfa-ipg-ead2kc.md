# IPG EAD-2K-C: read-only integration contract (M1)

**Source:** photographs of the serialized amplifier's manual (IPG Photonics,
P21-006000 Rev A, pages 27-32; P21-008000 Rev A, pages 33-34),
and the unit's nameplate (model EAD-2K-C; serial FA160101).
These photographs are not included in the repository.

## Scope and safety boundary

This milestone contains typed EDFA telemetry, a deterministic simulator, a
pure 32-bit status decoder, and a **query-only GPIB codec**. It does not import
PyVISA, open a VISA resource, scan a GPIB bus, operate the EDFA, connect any
instrument, change amplifier settings, or modify the IOPanel GUI.

The laboratory EDFA has a documented optical operating wavelength range of
1535-1565 nm and input-power range -6 to +10 dBm (specification page 33).
The manual warns not to enable emission without seed light (page 15). These
limits are *not* a complete automatic-emission safety procedure. A physical
interlock, wiring, operator authorization, seed-loss behavior, and optical
path protections require separate supervised qualification.

The GPIB connection is already cabled to the laboratory workstation, but
the exact address, installed VISA runtime, GPIB query framing/termination,
response syntax and remote/local effects must be checked before M2.
Never infer readiness or emission-on/off from a successful session open.

## GPIB query inventory (manual pages 31-32)

| Command | Semantic result | Unit / caution |
| --- | --- | --- |
| `*IDN?` | Identity string | Preserve raw string; actual response unqualified |
| `SOUR:POW:STAT?` | Device status word | Page 32 refers to RS-232 status semantics on page 29 |
| `INP?` | Input optical power | mW or dBm depending on status bit 18 |
| `INP:LIM:LOW?` | Minimum input-power threshold | mW or dBm; not a calibrated seed-power reading |
| `SOUR:POW?` | Output optical power | W or dBm, bit 18 |
| `SOUR:POW:SPO?` | Power setpoint | W or dBm, bit 18 |
| `SOUR:POW:BACK?` | Back-reflection indicator | % of the instrument's maximum, not watts |
| `SOUR:CURR?` | Measured diode current | A |
| `SOUR:TEMP:LEV?` | Case temperature | degrees Celsius |

The codec intentionally does not expose `*CLS`, `*ESR?` (read-and-clear),
`*RST`, `*ESE`, `UNIT:POW`, `SOUR:POW:STAT`, `SOUR:POW`,
`SOUR:GAIN` or `SOUR:CURR`. The manual's `*RST` row has an
inconsistent `*RES` example; M1 does not need either.

Page 29 defines the status bits:

- 0 APC active; 9 AGC active; 16 ACC active (conflicting/absent modes remain unknown)
- 2 interlock open; 3 overtemperature; 4 input power low; 6 back-reflection alarm
- 5 emission **starting**, not confirmed steady emission; 14 three-second startup
- 17 RS-232 prompt; 18 dBm selected; 20 AGC available
- 21 input-power monitor available; 22 back-reflection monitor available
- Other bits reserved; always preserve the entire unsigned 32-bit word

The status may change between sequential GPIB queries, so a telemetry snapshot
is *not* a simultaneous/atomic observation. The GPIB status example format is
not fully illustrated: the decoder accepts bare decimal integers and
`STA: <decimal>` consistent with the RS-232 status table, but the actual
device response must be recorded and verified before commissioning.
Unexpected status formats are rejected rather than guessed.

The input-monitor capability bit gates querying input power and its threshold.
The back-reflection-monitor capability bit gates the back-reflection query.
The device's internal input monitor is described as indicative in the manual;
it is not an independent calibrated seed interlock.

## Boundaries and next milestone

`hardware/edfa_gpib.py` accepts an injected object with a
`query(message: str) -> str` method. This permits deterministic fake
sessions and eventually an explicitly opened VISA session, but M1 contains
**no physical VISA backend**. The protocol codec owns no session and cannot
send an arbitrary command through its public API.

`DummyEDFA` implements the shared read-only `AbstractEDFA` contract;
it never simulates an unsafe automatic optical restart. Its fault injection
is an offline test facility, not a model validated against EDFA firmware.

M2 should separately establish the actual GPIB resource/address, VISA
installation, termination/EOI, query timeouts, error response behavior,
remote/local changes, read-only command compatibility, and vendor/physical
status interpretation. All real connection attempts require a supervised
operator-approved procedure. No live physical operation or completed
laboratory qualification is claimed in M1.

RS-232 is intentionally deferred; preserve a transport-neutral semantic
interface so it can be added later without duplicating scientific state.
