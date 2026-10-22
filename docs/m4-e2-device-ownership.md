# M4-E2A — Logical device lease protocol (not yet wired to hardware)

## Existing ownership architecture examined

Current IOPanel already tracks CT400 operations in `MainWindow` using
`CT400OperationState` (IDLE, CONNECTING, DISCONNECTING, SCANNING,
MONITORING, ALIGNMENT), plus calibration-specific reservations in
`CT400ControlPanel`. The PM5020 backend is **created and owned inside**
`PM5020MonitorWorker`'s long-lived QThread. Requests arrive via signals and
must not directly call that backend from a different thread.

The M4-E1 simulated coordinator is not registered with either existing
worker and MUST NOT be promoted to physical hardware by changing a type
check. Two separate application-level ownership paths cannot safely reach
into the same CT400 and PM5020 objects without a planned protocol.

## Delivered here — portable reservation primitives only

`logic.device_lease_registry.DeviceLeaseRegistry` provides:

- Atomic, nonblocking all-or-nothing reservations over CT400 and PM5020.
- Unique, identity-checked lease tokens that prevent stale GUI callbacks or
  other registries from releasing an unrelated newer lease.
- Explicit `request_cancel` without auto-release or native interruption.
- Explicit `release(..., operations_complete=True)` **only when each worker
  has acknowledged it finished**. No timeout-driven release or queue bypass.
- `mark_uncertain` quarantining *every resource* in a lease if native
  completion, trigger disarming, or device shutdown is uncertain.
- `confirm_recovery(..., operator_confirmed=True)` as a declaration from
  an independently supervised recovery process. It is never automatic and
  does not itself read a device or verify physical recovery.
- Immutable, thread-safe status snapshots to support a future Qt adapter.

This service deliberately creates **no connections, native threads, timers,
laser emission, DLL loading, Qt objects or device calls**. It has no implicit
global instance and is not imported from the existing GUI. Its tests use
only Python threads and deterministic state transitions.

**Qualification:** E2A validates the *arbitration state machine*, NOT
actual device exclusivity. Current CT400 and PM5020 UI workers remain
unmodified and do not yet consult this registry.

## E2B integration design — required before physical coordinated acquisition

1. Assign **one registry per MainWindow**, created before instrument
   initialization, and propagate reservation authority through explicit
   queued Qt protocols rather than exposing native device objects.
2. Map all existing CT400 state transitions (including calibration and
   alignment), and PM5020 monitor connect/configure/poll/stop/disconnect,
   to the same registry. Never perform cross-thread PM5020 native calls.
3. A coordinated request must reserve both resources **atomically before**
   either owner worker receives an arm/scan command; fail quickly if an
   existing monitor, scan, alignment or calibration is active.
4. PM5020 owner worker must acknowledge monitoring stopped and no queued
   polling read remains before granting burst configuration. Paused
   monitoring is **still an owned session**, not automatically free.
5. PM5020 must acknowledge successful arming before CT400 worker is
   authorized to start. Both workers acknowledge final completion and
   output ownership before the lease can be released.
6. A CT400 stop request may race with native ScanStart/ScanWaitEnd. The
   vendor does not guarantee DLL reentrancy. Define a bounded
   *notification/observation* protocol, not a forceful thread termination.
   If a native call remains blocked, mark the lease UNCERTAIN, keep it
   reserved and require an operator-controlled recovery path.
7. Native PM5020 burst retrieval is single-call only with the verified
   patched x64 DLL. Preserve full CH1/CH2 arrays and original timestamps.
   Keep `timing_qualified=False`, native ticks without assumed units,
   and manufacturer-supported, **provisional** `index_base=0` until the
   cross-instrument edge/buffer relationship is separately validated.
8. Preserve all CT400 warning codes and raw sync-file indices. Continue
   excluding the EDFA/optical amplification from synchronized M4.
9. Test Qt queued-signal ordering, stale generation suppression,
   shutdown while a lease is in flight, failures on each worker thread,
   monitor-vs-burst contention, and cancellation during native waits
   using only synthetic hardware. Only then plan supervised lab trials.

## Non-goals / stop conditions

Do not merge this primitive as proof of physical safety. Do not wire M4-E1
directly to `MainWindow.ct400_device` or `PM5020Panel.worker._backend`;
both would bypass established ownership. Do not automatically release a
lease if a native call times out, the GUI closes, or a worker disconnects
without acknowledged completion. Do not turn on lasers or EDFA, operate
instruments, or commit proprietary SDK binaries during E2A.
