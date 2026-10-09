"""Pure formatting for the compact global instrument status indicators."""

from dataclasses import dataclass
from enum import Enum

from logic.matlab_engine_manager import MatlabEngineState


@dataclass(frozen=True)
class StatusText:
    text: str
    tooltip: str
    state: str


class CameraStatus(Enum):
    DISABLED = "disabled"
    INITIALIZING = "initializing"
    UNAVAILABLE = "unavailable"
    STREAMING = "streaming"
    RECOVERING = "recovering"
    SIMULATED = "simulated"


def format_ct400_status(
    state: Enum,
    *,
    simulated: bool = False,
    input_name: str | None = None,
    operation: Enum | None = None,
) -> StatusText:
    if simulated:
        return StatusText("CT400: SIMULATED", "CT400 simulation backend", "simulated")
    labels = {
        "UNKNOWN": ("Initializing", "CT400 initialization is in progress", "initializing"),
        "UNAVAILABLE": ("Unavailable", "CT400 backend is unavailable", "unavailable"),
        "DISCONNECTED": (
            "Ready (Disconnected)",
            "CT400 initialized successfully; no laser input is connected",
            "disconnected",
        ),
        "CONNECTING": ("Connecting", "CT400 laser connection is in progress", "busy"),
        "DISCONNECTING": ("Disconnecting", "CT400 laser disconnection is in progress", "busy"),
        "CONNECTED": (
            f"Connected · {input_name}" if input_name else "Connected · input unknown",
            "Laser connection is authorized for the recorded CT400 input"
            if input_name
            else "CT400 reports connected, but no authorized input is recorded",
            "connected",
        ),
        "ERROR": ("Error", "CT400 native operation failed; physical state may require recovery", "error"),
        "DISCONNECT_UNCONFIRMED": ("State uncertain", "CT400 physical disconnect is unconfirmed", "error"),
        "SCAN_STATE_UNCERTAIN": ("Scan uncertain", "CT400 scan state requires operator recovery", "error"),
        "RECOVERY_CONFIRMATION_REQUIRED": (
            "Confirm safe state",
            "Recovery command returned; physical safe state still requires operator confirmation",
            "error",
        ),
    }
    text, tooltip, status = labels[getattr(state, "name", str(state))]
    if getattr(operation, "name", "") in ("CONNECTING", "DISCONNECTING"):
        text = operation.name.title()
        status = "busy"
    return StatusText(f"CT400: {text}", tooltip, status)


def format_cameras_status(states: list[CameraStatus]) -> StatusText:
    if not states:
        return StatusText("Cameras: Disabled", "All cameras are disabled in configuration", "disabled")
    if CameraStatus.RECOVERING in states and len(set(states)) == 1:
        return StatusText("Cameras: Recovering", "At least one camera is recovering", "busy")
    unique = set(states)
    if unique == {CameraStatus.INITIALIZING}:
        return StatusText("Cameras: Initializing", "Enabled cameras are initializing", "initializing")
    if unique == {CameraStatus.SIMULATED}:
        return StatusText("Cameras: Simulated", "All enabled cameras use the simulation backend", "simulated")
    if unique == {CameraStatus.STREAMING}:
        return StatusText("Cameras: Streaming", "All enabled cameras are streaming", "ready")
    if unique == {CameraStatus.UNAVAILABLE}:
        return StatusText("Cameras: Unavailable", "Enabled cameras failed to initialize or stream", "error")
    counts = {state: states.count(state) for state in unique}
    detail = "Camera states: " + ", ".join(f"{state.value}: {count}" for state, count in counts.items())
    return StatusText("Cameras: Mixed", detail, "mixed")


def format_matlab_status(state: MatlabEngineState) -> StatusText:
    labels = {
        MatlabEngineState.UNAVAILABLE: ("Unavailable", "MATLAB Engine is not available", "unavailable"),
        MatlabEngineState.IDLE: ("Idle", "MATLAB Engine is available but has not started", "idle"),
        MatlabEngineState.STARTING: ("Starting", "MATLAB Engine startup is in progress", "busy"),
        MatlabEngineState.READY: ("Ready", "MATLAB Engine is ready", "ready"),
        MatlabEngineState.FAILED: ("Failed", "MATLAB Engine startup failed", "error"),
        MatlabEngineState.SHUTTING_DOWN: ("Stopping", "MATLAB Engine is shutting down", "busy"),
    }
    text, tooltip, status = labels[state]
    return StatusText(f"MATLAB: {text}", tooltip, status)


def format_activity_status(operation: Enum) -> StatusText:
    labels = {
        "IDLE": ("Idle", "No CT400 operation is active", "idle"),
        "CONNECTING": ("Connecting", "CT400 connection is active", "busy"),
        "DISCONNECTING": ("Disconnecting", "CT400 disconnection is active", "busy"),
        "SCANNING": ("Scanning", "CT400 scan is active", "busy"),
        "MONITORING": ("Monitoring", "CT400 power monitoring is active", "busy"),
        "ALIGNMENT": ("Aligning", "Authorized alignment operation is active", "busy"),
    }
    text, tooltip, state = labels[getattr(operation, "name", str(operation))]
    return StatusText(f"Activity: {text}", tooltip, state)
