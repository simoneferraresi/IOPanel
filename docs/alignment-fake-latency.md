# Offline alignment command/timing characterization — Issue #157

Run on a driver-free Python 3.12 environment:

```powershell
uv run --no-sync python -m tools.characterize_alignment_latency --output alignment-fake.json
```

This executes **the actual** `AlignmentWorker.run_mapping`, `PiezoController.set_voltage` and voltage-range/read functions against an in-memory fake DLL and fake CT400. No device discovery, WinDLL loading, laser operation or motion occurs.

The fake backend uses a virtual clock. It advances when the real worker requests Qt sleeps, when the fake DLL is called, or when the fake detector is read. This makes large 100×100 maps testable without delaying CI for minutes. The emitted `python_overhead_ms_wall` measures host Python execution and **excludes virtual device delays**; it is not a prediction of physical acquisition time.

The command model is checked against the actual executed calls, not merely copied into a formula:

- For P points: 2P axis writes, 4P in-setter bounds reads, 4 initial bounds reads, 2 voltage reads, and 4 restoration writes with 8 bounds reads = **6P + 18 fake DLL calls** on successful maps.
- For S samples per point: **P × S** synthetic detector reads.
- Requested waits: 200 ms laser stabilization + P × settling_ms + (20 × P × S ms if S > 1).
- Normal-path restoration currently repeats the stage write in the success branch and `finally` block. Some raster axis writes are unchanged/no-op; the generated report counts them as **hypotheses**, not as candidates already safe to remove.
- Synthetic power intentionally depends differently on X and Y, asserting both power-grid orientation and the original X/Y units.

Failures/cancellation use separate synthetic regression tests to check stage restoration and laser-disable request.

**Decision:** No changes to physical voltage commands, measurement ordering, bounds, settling, averaging, speed or motion trajectories. No real-hardware throughput claim. Candidates such as caching DLL range reads, no-op suppression and duplicate restoration require independent vendor/laboratory acceptance before altering a command path. Fine/spiral routines can be assessed using the same fake interfaces if a future physical performance problem justifies additional investment.

Physical Alignment safety/positive-path qualification remains tracked by #144.

For fine and spiral alignment, the same model executes the real algorithms on a **flat -30 dBm synthetic response** using one hill-climb iteration and a bounded small spiral. Output separately records observed fake calls and waits; these are fixture-specific, not general bounds on arbitrary power landscapes or real devices.
