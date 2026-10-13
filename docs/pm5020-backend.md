# PM5020 backend

The PM5020 backend provides a small device contract, a deterministic simulator,
and an optional physical adapter. It does not add a GUI panel or coordinate a
CT400 scan.

## API and data

`TLPMXPM5020` and `DummyPM5020` implement `AbstractPM5020`. Channel readings
and burst power values are in watts. Single-reading timestamps are timezone
aware UTC datetimes. Burst timestamps are retained as unsigned 32-bit
instrument ticks: the reference notebook does not document their time scale or
epoch. Burst results carry separate CH1 and CH2 arrays with an enforced shared
sample count.

The physical adapter loads `TLPMX` only when `connect(resource_name)` is called.
Pass the USB/VISA resource string from configuration; the laboratory notebook's
resource identifier is not a default. A single lock serializes all wrapper
calls. Once a burst is armed, monitoring reads are rejected until retrieval
completes. Failed or cancelled physical burst retrieval requires closing and
reconnecting to establish a known state.

## Verified burst behavior

The authoritative laboratory notebook's third-to-last code cell sets CW mode,
disables autorange and the input filter, configures power measurement on both
channels, and sets trigger source 3, delay 0, one sample per trigger, and one
average. It calls `startBurstArrayMeasurement`, gets the recorded count through
`getBurstArraySamplesCount(byref(c_uint32))`, and retrieves timestamp (`c_uint32`)
and CH1/CH2 power (`c_float`) buffers with `getBurstArraySamples` in 100-sample
chunks. The configured safety capacity is 15,000 samples; the adapter validates
the count and allocates each chunk to its requested length, including a partial
last chunk.

The notebook comment says `getBurstArraySamples()` aborts the measurement, but
the comment alone does not establish that as a documented wrapper guarantee.
This backend does not depend on the abort as its completion signal. Physical
behavior, timestamps, trigger edges, chunk retrieval, and recovery still need
validation with the laboratory instrument before experimental use.

Single-channel optical power reads use the official Thorlabs Python example's
`measPower(byref(c_double), channel)` signature. The official PM5020 example
also demonstrates channel-specific wavelength and range settings. Unsupported
sensor discovery is not inferred: sensor absence is reported by the driver
operation that fails.

## Windows requirements

Install Thorlabs Optical Power Meter software/SDK on the laboratory Windows
machine. It supplies the vendor `TLPMX.py` ctypes wrapper and the native
`TLPMX_32.dll` or `TLPMX_64.dll`, together with VISA runtime components. Match
the native DLL to the Python process bitness and make sure the vendor DLL is
loadable through the SDK's normal installation paths. The wrapper is locally
supplied by Thorlabs and is not a PyPI dependency; IOPanel does not bundle it.

The simulator and all backend imports work without that SDK. Unit tests use a
fake wrapper and do not establish compatibility with a physical meter.

## Future panel use

Call `read_power(channel)` from an existing background worker on a chosen
interval. The adapter performs one blocking measurement call per request; it
does not create a polling thread or touch Qt. The future panel should own one
backend instance and submit monitoring and burst operations to one serialized
worker, since vendor DLL calls are synchronous and Qt thread termination cannot
interrupt them. Physical cancellation is checked between burst calls only; it
cannot preempt a blocking native call.
