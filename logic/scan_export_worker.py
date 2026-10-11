"""One-shot, hardware-independent schema-v2 scan CSV/MAT export worker.

Stages each format beside its final path. A successfully written stage is
published atomically; absent destinations are published with an exclusive hard
link to avoid an unintended overwrite after the GUI's existence check.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy.io as sio
from PySide6.QtCore import Signal, Slot

from logic.scan_export import ScanExportPayload
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.scan_export_worker")


@dataclass(frozen=True)
class ScanExportOutcome:
    saved: tuple[Path, ...]
    errors: tuple[str, ...]


def _write_staged_format(
    name: str, destination: Path, payload: ScanExportPayload, *, overwrite_confirmed: bool
) -> None:
    """Write only a complete file and never overwrite without authorization.

    A successful link is exclusive for an originally absent destination,
    including if some other process creates the destination during staging.
    The stage is on the same filesystem as the target for atomic publication.
    """
    fd, staged_name = tempfile.mkstemp(prefix=".iopanel-scan-", suffix=".tmp", dir=destination.parent)
    stage = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            if name == "CSV":
                import io

                with io.TextIOWrapper(raw, encoding="utf-8", newline="", write_through=True) as stream:
                    np.savetxt(
                        stream,
                        payload.csv_data,
                        delimiter=",",
                        header=payload.csv_header,
                        comments="",
                        fmt="%.6f",
                    )
                    stream.flush()
                    os.fsync(stream.buffer.fileno())
                # TextIOWrapper closes the underlying descriptor.
            elif name == "MAT":
                sio.savemat(raw, payload.mat_data, do_compression=True)
                raw.flush()
                os.fsync(raw.fileno())
            else:
                raise ValueError(f"Unsupported scan export format: {name}")
        if overwrite_confirmed:
            os.replace(stage, destination)
        else:
            # os.link fails with FileExistsError rather than replacing a target.
            os.link(stage, destination)
    finally:
        stage.unlink(missing_ok=True)


class ScanExportWorker(BaseWorker):
    """Write the GUI-authorized immutable payload without accessing Qt widgets."""

    outcome = Signal(object)

    def __init__(
        self,
        payload: ScanExportPayload,
        targets: dict[str, Path],
        confirmed_overwrites: frozenset[Path],
    ) -> None:
        super().__init__()
        self._payload = payload
        self._targets = dict(targets)
        self._confirmed_overwrites = confirmed_overwrites

    @Slot()
    def run(self) -> None:
        saved: list[Path] = []
        errors: list[str] = []
        try:
            for name, path in self._targets.items():
                try:
                    _write_staged_format(
                        name, path, self._payload, overwrite_confirmed=path in self._confirmed_overwrites
                    )
                    saved.append(path)
                except Exception as exc:
                    logger.exception("%s scan export failed: %s", name, path)
                    errors.append(f"{name}: {exc}")
            self.outcome.emit(ScanExportOutcome(tuple(saved), tuple(errors)))
        finally:
            self.finished.emit()
