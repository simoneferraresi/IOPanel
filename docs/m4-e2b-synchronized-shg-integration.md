# M4-E2B — synchronized CT400/PM5020 SHG, simulator-only acceptance

This is a **software integration gate**, not approval to conduct a physical
synchronized experiment. Neither a physical CT400 nor a physical PM5020 nor an
EDFA may be operated through the SHG tab. The application checks concrete
DummyCT400 and simulation PM5020 configurations before any owner-thread
request; the PM owner also refuses a backend that is not DummyPM5020.

## Qt ownership sequence

1. MainWindow maintains the one DeviceLeaseRegistry. Ordinary CT400 operations,
   alignment and monitoring hold CT400 leases; a connected/paused PM5020 holds
   its own owner-QThread lease.
2. Pressing "Start simulated synchronized SHG capture" locks the PM5020 UI and
   queues quiescence to the *existing* PM5020MonitorWorker thread. A paused
   monitor is not considered released. Queued polling is drained, the simulator
   is closed, and the standalone PM5020 lease is released only after this
   successful close. A request-specific quiescence acknowledgement follows.
3. The GUI coordinator rechecks CT400 availability and atomically reserves both
   CT400 and PM5020. Conflicts never claim just one instrument. An identity
   token and PM connection generation key each callback; stale completions
   cannot advance or release a new run.
4. The PM owner QThread constructs/configures/arms DummyPM5020 and acknowledges
   the ARMED state. Only then, after the state machine confirms PM arming, does
   the controller start its one dedicated DummyCT400 scan worker QThread.
   The CT worker performs CT400 setup, simulated sync enabling, scan wait,
   vendor-code classification, processed-data read and sync disabling.
5. After the CT scan thread finishes and acknowledges cleanup, PM burst
   retrieval runs in the original PM owner QThread, retaining raw uint32 ticks
   and both channel powers in watts. PM cleanup closes the simulator, and the
   second owner acknowledgement arrives.
6. Only after both cleanup acknowledgements may the coordinator release the
   atomic lease and transactionally archive the data. On any failure/cancel,
   no plausible completed archive is written.

## Cancellation, timeout and close

- Cancellation is a request. The simulator-only CT stop path cooperates with
  its blocking wait; it does not terminate QThreads. Any queued work must still
  acknowledge cleanup before normal lease release.
- A 30-second owner-response timeout quarantines the joint lease as UNCERTAIN.
  Eventual callbacks cannot free it. Operator-confirmed recovery is a
  *separate* explicit action, never a timeout-triggered reset. Confirming a
  physical safe-state requires the approved laboratory procedure.
- MainWindow defers shutdown until a pending synchronized session retires
  both worker owners. No PM driver handle is called from the GUI thread.
- A failed/disputed PM close or CT stop/sync-output disable is not treated
  as successful cleanup.

## Archive provenance

M4-E2B uses the existing transactional synchronized archive writer with
`milestone: M4-E2B`: CT400 processed detector values, the original simulated
sync-text payload, every PM5020 raw burst tick/CH1/CH2 watt reading, and
SHA-256 digests for each file. Original CT400 warning and raw status code are
kept. `timing_qualified=false` and provisional `index_base=0` are essential:
neither sample times nor cross-instrument alignment has been calibrated.

## Physical qualification is outside M4-E2B

Do not enable the feature on physical instruments based on green CI. Bench
qualification must establish PM tick units, trigger edge/polarity, CT400
sync-file-to-PM offset and latency, timeouts/recovery and independent optical
safe-state, with supervised lab permission. EDFA remains out of scope.

## Regression tests

Hardware-independent CI exercises Qt competing scans/monitoring, paused PM
handoff, stale callbacks, successful archive audit, cancellation, timeout
quarantine, close during a pending run, recoverable PM arm/retrieve faults,
physical-backend rejection, and existing alignment/connection lifecycle tests.
Physical driver/DLL and live optical operation are absent from these tests.
