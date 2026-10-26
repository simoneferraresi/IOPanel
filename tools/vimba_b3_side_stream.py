"""Bounded, read-only-settings streaming check for the approved Side camera.

Run with the laboratory's installed system Python in an ordinary Windows
session. This script never changes camera features or saves image data.
"""

from __future__ import annotations

# Support both "python tools/..." and "python -m tools...." invocations.
if __package__:
    from . import _vimba_stream_diagnostic as _diagnostic
else:
    import _vimba_stream_diagnostic as _diagnostic

APPROVED_CAMERA_ID = "DEV_000F315BA8F9"
TARGET_COMPLETE_FRAMES = _diagnostic.TARGET_COMPLETE_FRAMES
MAX_ACQUISITION_SECONDS = _diagnostic.MAX_ACQUISITION_SECONDS
FRAME_BUFFER_COUNT = _diagnostic.FRAME_BUFFER_COUNT
DISCOVERY_WAIT_SECONDS = _diagnostic.DISCOVERY_WAIT_SECONDS

# Preserve the former diagnostic utility functions for existing script consumers.
read_feature = _diagnostic.read_feature
display_value = _diagnostic.display_value
managed_context = _diagnostic.managed_context


def main() -> int:
    return _diagnostic.run_stream_diagnostic(APPROVED_CAMERA_ID, "Side", description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
