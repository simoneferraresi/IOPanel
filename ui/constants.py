"""
Centralized constants for the UI to avoid magic strings and numbers.
This file defines a shared "language" between different UI components.
"""

# =============================================================================
# Object Names for QSS Styling and Widget Identification
# =============================================================================
ID_SCAN_BUTTON = "scanButton"
ID_MONITOR_BUTTON = "monitorButton"
ID_CT400_SCAN_PANEL = "ct400ScanPanel"
ID_CT400_MONITOR_PANEL = "ct400MonitorPanel"
ID_CT400_STATUS_LABEL = "ct400StatusLabel"


# =============================================================================
# Dynamic Property Names for Styling and Logic
# =============================================================================
PROP_SCANNING = "scanning"
PROP_MONITORING = "monitoring"
PROP_STATUS = "status"


# =============================================================================
# Operation Types for Worker Threads and Signals
# =============================================================================
OP_AUTO_EXPOSURE = "auto_exposure"
OP_AUTO_GAIN = "auto_gain"


# =============================================================================
# UI Timing and Behavior Constants
# =============================================================================
MONITOR_TIMER_INTERVAL_MS = 250
HISTOGRAM_UPDATE_INTERVAL_MS = 50
CAMERA_WATCHDOG_INTERVAL_MS = 3000
CAMERA_RESIZE_EVENT_THROTTLE_MS = 100  # Minimum time between resize event processing
CAMERA_RESIZE_UPDATE_DELAY_MS = 50  # Delay after a resize event before redrawing


# =============================================================================
# Shared UI Status Messages
# =============================================================================
MSG_CAMERA_CONNECTING = "Connecting to\n{}"
MSG_CAMERA_WAITING = "Waiting for frames..."
MSG_CAMERA_FAILED = "{}\n(Failed to Open)"
MSG_SCAN_READY = "Ready"
MSG_SCAN_CANCELLED = "Scan cancelled by user."
MSG_SCAN_FINISHED = "Scan finished."
