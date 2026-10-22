"""Audit a saved M4 CT400/PM5020 burst directory without opening instruments.

Run: python -m tools.audit_pm5020_sync_run PATH_TO_RUN_DIRECTORY
"""

from __future__ import annotations

import argparse
import json

from logic.pm5020_capture_audit import CaptureAuditError, audit_pm5020_burst_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline PM5020 burst / CT400 sync file quality audit")
    parser.add_argument("run_directory", help="Folder containing raw CSV, sync log, and JSON metadata")
    args = parser.parse_args()
    try:
        report = audit_pm5020_burst_run(args.run_directory)
    except (CaptureAuditError, OSError) as exc:
        parser.exit(2, f"CAPTURE AUDIT FAILED: {exc}\n")
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    print(
        "NOTICE: Both index bases are candidate interpretations only. "
        "No trigger timing, timestamp-unit or optical safety certification is implied."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
