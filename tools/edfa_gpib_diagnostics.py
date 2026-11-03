"""Operator-gated, bounded, read-only EAD-2K-C communication experiments.

Dry runs do not instantiate VISA. Physical sessions require explicit approval.
Only the existing SCPI read-query allowlist is permitted; all direct setters,
resets and bus-clear operations are rejected by the recording session.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from config_model import AppConfig
from hardware.edfa_gpib import _READ_QUERIES
from hardware.ipg_ead2kc import IPGEAD2KCGPIB, _default_manager_factory
from tools.edfa_readonly_probe import read_configuration


class ManagerFactory(Protocol):
    def __call__(self) -> Any: ...


@dataclass
class _Recorder:
    max_queries: int
    deadline: float
    delay_s: float
    start: float = field(default_factory=time.monotonic)
    count: int = 0
    records: list[dict[str, object]] = field(default_factory=list)

    def record(self, kind: str, *, session: int, **details: object) -> None:
        self.records.append(
            {
                "elapsed_ms": round((time.monotonic() - self.start) * 1000, 3),
                "event": kind,
                "session": session,
                "thread": threading.get_ident(),
                **details,
            }
        )

    def check_budget(self) -> None:
        if time.monotonic() - self.start > self.deadline:
            raise RuntimeError("Read-only diagnostic duration budget exceeded; stopped")


class _QueryOnlySession:
    """Only the pre-qualified SCPI queries can leave this VISA wrapper."""

    def __init__(self, raw: Any, recorder: _Recorder, session_number: int) -> None:
        self._raw = raw
        self._log = recorder
        self._session = session_number
        self._first_query = True

    @property
    def timeout(self) -> int:
        return self._raw.timeout

    @timeout.setter
    def timeout(self, value: int) -> None:
        self._raw.timeout = value

    @property
    def write_termination(self) -> str | None:
        return self._raw.write_termination

    @write_termination.setter
    def write_termination(self, value: str | None) -> None:
        self._raw.write_termination = value

    @property
    def read_termination(self) -> str | None:
        return self._raw.read_termination

    @read_termination.setter
    def read_termination(self, value: str | None) -> None:
        self._raw.read_termination = value

    def query(self, command: str) -> str:
        # PyVISA may internally viWrite the QUERY; this is not a setter.
        if command not in _READ_QUERIES:
            raise ValueError(f"Unapproved EDFA read-only command: {command!r}")
        self._log.check_budget()
        if self._log.count >= self._log.max_queries:
            raise RuntimeError("Read-only query-count budget exhausted; stopped")
        if not self._first_query and self._log.delay_s:
            time.sleep(self._log.delay_s)
            self._log.check_budget()
        self._first_query = False
        self._log.count += 1
        index = self._log.count
        self._log.record("query_start", session=self._session, index=index, command=command)
        try:
            reply = self._raw.query(command)
        except Exception as exc:
            self._log.record(
                "query_error",
                session=self._session,
                index=index,
                command=command,
                error_type=type(exc).__name__,
                error=str(exc),
                visa_code=getattr(exc, "error_code", None),
            )
            raise
        self._log.record(
            "query_success",
            session=self._session,
            index=index,
            command=command,
            reply=reply[:160] if isinstance(reply, str) else repr(reply)[:160],
        )
        return reply

    def write(self, _message: str) -> None:
        raise RuntimeError("Diagnostic session forbids all direct physical setter writes")

    def close(self) -> None:
        self._log.record("session_close_start", session=self._session)
        try:
            self._raw.close()
        except Exception as exc:
            self._log.record("session_close_error", session=self._session, error=repr(exc))
            raise
        self._log.record("session_close_success", session=self._session)


class _QueryOnlyManager:
    def __init__(self, raw: Any, recorder: _Recorder, session_number: int) -> None:
        self._raw = raw
        self._log = recorder
        self._session = session_number

    def open_resource(self, resource_name: str) -> _QueryOnlySession:
        self._log.check_budget()
        self._log.record("session_open_start", session=self._session, resource=resource_name)
        try:
            raw_session = self._raw.open_resource(resource_name)
        except Exception as exc:
            self._log.record("session_open_error", session=self._session, error=repr(exc))
            raise
        self._log.record("session_open_success", session=self._session)
        return _QueryOnlySession(raw_session, self._log, self._session)

    def close(self) -> None:
        self._log.record("manager_close_start", session=self._session)
        try:
            self._raw.close()
        except Exception as exc:
            self._log.record("manager_close_error", session=self._session, error=repr(exc))
            raise
        self._log.record("manager_close_success", session=self._session)


def run_diagnostics(
    config: AppConfig,
    *,
    connect_readonly: bool = False,
    operator_confirmed: bool = False,
    plan: str = "telemetry-configuration",
    sessions: int = 1,
    interquery_delay_ms: int = 0,
    max_queries: int = 60,
    max_seconds: float = 60.0,
    manager_factory: ManagerFactory = _default_manager_factory,
) -> dict[str, object]:
    """Perform bounded operator-approved queries; never retry a failed transaction."""
    if plan not in ("telemetry", "telemetry-configuration"):
        raise ValueError("Unknown read-only plan")
    if not (1 <= sessions <= 3 and 1 <= max_queries <= 60 and 5 <= max_seconds <= 120):
        raise ValueError("Session, query or duration budget outside the approved bounds")
    if interquery_delay_ms not in (0, 10, 50, 100):
        raise ValueError("Select a predeclared diagnostic delay: 0, 10, 50 or 100 ms")
    profile = config.instruments
    if not connect_readonly:
        return {"mode": "dry-run", "message": "No VISA session created or SCPI command transmitted"}
    if not operator_confirmed:
        raise ValueError("Physical diagnostics require --operator-confirmed")
    if profile.edfa_backend != "physical" or not profile.edfa_gpib_resource_name:
        raise ValueError("A reviewed, explicitly addressed physical EDFA configuration is required")
    if profile.edfa_m4_write_enabled:
        raise ValueError("Refusing diagnostics with the physical M4 write policy enabled")

    recorder = _Recorder(max_queries, max_seconds, interquery_delay_ms / 1000)
    report: dict[str, object] = {
        "mode": "physical-readonly-diagnostic",
        "status": "FAILED",
        "resource": profile.edfa_gpib_resource_name,
        "started_utc": datetime.now(UTC).isoformat(),
        "plan": plan,
        "sessions_planned": sessions,
        "interquery_delay_ms": interquery_delay_ms,
        "max_queries": max_queries,
        "max_seconds": max_seconds,
        "optical_state": "NOT VERIFIED BY SOFTWARE",
        "parameter_writes": "FORBIDDEN",
        "visa_timeout_ms": profile.edfa_gpib_timeout_ms,
        "read_termination": profile.edfa_gpib_read_termination,
        "write_termination": profile.edfa_gpib_write_termination,
    }
    try:
        for session_number in range(1, sessions + 1):
            recorder.check_budget()

            def factory(session_number: int = session_number) -> _QueryOnlyManager:
                return _QueryOnlyManager(manager_factory(), recorder, session_number)

            device = IPGEAD2KCGPIB(
                profile.edfa_gpib_resource_name,
                timeout_ms=profile.edfa_gpib_timeout_ms,
                write_termination=profile.edfa_gpib_write_termination,
                read_termination=profile.edfa_gpib_read_termination,
                manager_factory=factory,
            )
            try:
                device.connect()
                report["identity"] = device.identify()
                device.read_telemetry()
                if plan == "telemetry-configuration":
                    device.read_configuration()
            finally:
                device.close()
        report["status"] = "PASSED_READ_ONLY_ONLY"
    except Exception as exc:  # noqa: BLE001 - preserve actual VISA failure in audit report.
        report["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "cause": repr(exc.__cause__) if exc.__cause__ else None,
        }
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        report["completed_queries"] = recorder.count
        report["events"] = recorder.records
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.ini"))
    parser.add_argument("--connect-readonly", action="store_true")
    parser.add_argument("--operator-confirmed", action="store_true")
    parser.add_argument("--plan", choices=("telemetry", "telemetry-configuration"), default="telemetry-configuration")
    parser.add_argument("--sessions", type=int, default=1)
    parser.add_argument("--interquery-delay-ms", type=int, default=0)
    parser.add_argument("--max-queries", type=int, default=60)
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--report-dir", type=Path, default=None)
    args = parser.parse_args()
    try:
        result = run_diagnostics(
            read_configuration(args.config),
            connect_readonly=args.connect_readonly,
            operator_confirmed=args.operator_confirmed,
            plan=args.plan,
            sessions=args.sessions,
            interquery_delay_ms=args.interquery_delay_ms,
            max_queries=args.max_queries,
            max_seconds=args.max_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - CLI reports failures without touching hardware.
        parser.exit(2, f"EDFA diagnostic refused: {type(exc).__name__}: {exc}\n")
    if args.connect_readonly:
        root = args.report_dir or (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "IOPanel" / "EDFA-GPIB-diagnostics"
        )
        root.mkdir(parents=True, exist_ok=True)
        name = datetime.now(UTC).strftime("edfa-gpib-%Y%m%dT%H%M%S%fZ.json")
        path = root / name
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"Diagnostic report: {path}")
    print(json.dumps({key: val for key, val in result.items() if key != "events"}, indent=2))
    return 0 if result.get("status") in (None, "PASSED_READ_ONLY_ONLY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
