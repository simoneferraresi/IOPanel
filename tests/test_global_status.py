from logic.matlab_engine_manager import MatlabEngineState
from ui.global_status import (
    CameraStatus,
    format_activity_status,
    format_cameras_status,
    format_ct400_status,
    format_matlab_status,
)
from ui.main_window import CT400OperationState, CT400Status


def test_ct400_status_separates_physical_simulated_and_uncertain_states():
    assert "Disconnected" in format_ct400_status(CT400Status.DISCONNECTED).text
    assert format_ct400_status(CT400Status.CONNECTED).text == "CT400: Connected"
    assert format_ct400_status(CT400Status.CONNECTED, simulated=True).text == "CT400: SIMULATED"
    assert "confirmation" in format_ct400_status(CT400Status.RECOVERY_CONFIRMATION_REQUIRED).tooltip
    assert format_ct400_status(CT400Status.SCAN_STATE_UNCERTAIN).state == "error"
    assert format_ct400_status(CT400Status.ERROR).state == "error"
    assert format_ct400_status(CT400Status.UNKNOWN).state == "initializing"
    assert format_ct400_status(CT400Status.UNAVAILABLE).state == "unavailable"


def test_camera_status_covers_disabled_initializing_failed_streaming_and_recovery():
    assert format_cameras_status([]).state == "disabled"
    assert format_cameras_status([CameraStatus.INITIALIZING]).state == "initializing"
    assert format_cameras_status([CameraStatus.UNAVAILABLE]).state == "error"
    assert format_cameras_status([CameraStatus.STREAMING]).state == "ready"
    assert format_cameras_status([CameraStatus.SIMULATED]).state == "simulated"
    assert format_cameras_status([CameraStatus.RECOVERING]).state == "busy"
    mixed = format_cameras_status([CameraStatus.STREAMING, CameraStatus.UNAVAILABLE])
    assert mixed.text == "Cameras: Mixed"
    assert format_cameras_status([CameraStatus.RECOVERING, CameraStatus.STREAMING]).text == "Cameras: Mixed"


def test_matlab_and_activity_status_cover_full_lifecycle():
    matlab = {
        MatlabEngineState.UNAVAILABLE: "unavailable",
        MatlabEngineState.IDLE: "idle",
        MatlabEngineState.STARTING: "busy",
        MatlabEngineState.READY: "ready",
        MatlabEngineState.FAILED: "error",
        MatlabEngineState.SHUTTING_DOWN: "busy",
    }
    for engine_state, expected in matlab.items():
        assert format_matlab_status(engine_state).state == expected

    operations = {
        CT400OperationState.IDLE: "Activity: Idle",
        CT400OperationState.CONNECTING: "Activity: Connecting",
        CT400OperationState.DISCONNECTING: "Activity: Disconnecting",
        CT400OperationState.SCANNING: "Activity: Scanning",
        CT400OperationState.MONITORING: "Activity: Monitoring",
        CT400OperationState.ALIGNMENT: "Activity: Aligning",
    }
    for operation, expected in operations.items():
        assert format_activity_status(operation).text == expected
