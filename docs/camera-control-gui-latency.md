# Offline camera-control GUI heartbeat study — Issue #153

Run in a driver-free Python 3.12 + Qt environment:

    uv run --no-sync python -m tools.characterize_camera_control_latency --output camera-controls-fake.json

Use a real Qt \`CameraPanel\` and its \`VimbaCam\` feature adapter, but attach only a Python in-memory fake feature device. The tool never initializes VmbSystem or opens a camera.

Five scenarios are measured independently with matching synthetic gain values: direct synchronous intermediate writes (reference to behavior before the merged coalescer, not a historical benchmark), current slider burst coalescing, typed immediate edit, two independent camera panels, and synthetic Qt QMutex contention while the GUI requests a feature write.

The heartbeat is a queued zero-timeout Qt callback posted immediately before the synchronous control operation. The reported median/p95/max delay measures the service time during which that callback could not run, **not** native camera frame rate, actual VmbPy latency, or a full continuous Qt frame-timing distribution. Output contains requested and completed synthetic write counts and verifies the final applied value. Results are environment-dependent; do not encode CI wall-clock thresholds.

## Decision

The PR #175 same-thread coalescer is a significant gain during slider drags because it avoids most intermediate synchronous setter calls. It does **not** make one remaining SDK call or a shared native mutex acquisition nonblocking; deliberate synthetic lock contention demonstrates the residual source of GUI delay. Sending feature calls to an arbitrary QThread would risk VmbPy/GenICam feature affinity, streaming callbacks, frame-buffer ownership, disconnect and late-call races. Consequently this characterization changes no production camera ownership code. Treat any future worker-dispatch proposal as a separate, vendor-supported hardware qualification effort.

Actual driver/runtime gains and camera behavior remain unqualified, and this is not a claim that camera-control latency is impossible to improve further. Close the investigation as a documented software no-go only if the issue owner accepts remaining SDK-threading/physical qualification as deferred.
