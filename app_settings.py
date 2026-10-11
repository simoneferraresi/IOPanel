"""Central, versioned persistence for harmless per-user UI preferences.

Hardware configuration remains in ``config.ini``. Values in this module are
limited to presentation preferences and paths used by file dialogs.
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings


class AppSettings:
    """Typed boundary around this application's QSettings namespace."""

    SCHEMA_VERSION = 1
    MAX_SPLITTER_SIZE = 1_000_000
    APPLICATION_ICON_IDS = frozenset({"optical_burst", "prism_spectrum"})

    def __init__(self, backend: QSettings | None = None) -> None:
        self._settings = backend if backend is not None else QSettings()
        raw_version = self._settings.value("_meta/schema_version", None)
        self.supported = raw_version is None or self._as_int(raw_version) == self.SCHEMA_VERSION
        if raw_version is None:
            self._settings.setValue("_meta/schema_version", self.SCHEMA_VERSION)
            self._settings.sync()

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    def _read(self, key: str, fallback: Any) -> Any:
        if not self.supported or not self._settings.contains(key):
            return fallback
        return self._settings.value(key)

    def _write(self, key: str, value: Any) -> None:
        if self.supported:
            self._settings.setValue(key, value)

    def sync(self) -> None:
        if self.supported:
            self._settings.sync()

    def geometry(self) -> QByteArray | None:
        value = self._read("MainWindow/geometry", None)
        return QByteArray(value) if isinstance(value, (QByteArray, bytes, bytearray)) else None

    def set_geometry(self, value: QByteArray) -> None:
        self._write("MainWindow/geometry", value)

    def splitter_sizes(self, count: int) -> list[int] | None:
        value = self._read("MainWindow/splitter_sizes", None)
        if not isinstance(value, (list, tuple)) or len(value) != count:
            return None
        sizes: list[int] = []
        for item in value:
            size = self._as_int(item)
            if size is None or size <= 0 or size > self.MAX_SPLITTER_SIZE:
                return None
            sizes.append(size)
        if sum(sizes) > self.MAX_SPLITTER_SIZE:
            return None
        return sizes

    def set_splitter_sizes(self, sizes: list[int]) -> None:
        self._write("MainWindow/splitter_sizes", [int(size) for size in sizes])

    def active_tab(self, tab_count: int, fallback: int = 0) -> int:
        value = self._as_int(self._read("MainWindow/active_tab", None))
        return value if value is not None and 0 <= value < tab_count else fallback

    def set_active_tab(self, index: int) -> None:
        self._write("MainWindow/active_tab", int(index))

    def log_console_visible(self, fallback: bool = False) -> bool:
        value = self._read("MainWindow/log_console_visible", None)
        return value if isinstance(value, bool) else fallback

    def set_log_console_visible(self, visible: bool) -> None:
        self._write("MainWindow/log_console_visible", bool(visible))

    def log_console_state(self) -> QByteArray | None:
        value = self._read("MainWindow/log_console_state", None)
        return QByteArray(value) if isinstance(value, (QByteArray, bytes, bytearray)) else None

    def set_log_console_state(self, value: QByteArray) -> None:
        self._write("MainWindow/log_console_state", value)

    def log_console_layout_version(self) -> int | None:
        return self._as_int(self._read("MainWindow/log_console_layout_version", None))

    def set_log_console_layout_version(self, version: int) -> None:
        self._write("MainWindow/log_console_layout_version", int(version))

    def log_console_geometry(self) -> QByteArray | None:
        value = self._read("MainWindow/log_console_geometry", None)
        return QByteArray(value) if isinstance(value, (QByteArray, bytes, bytearray)) else None

    def set_log_console_geometry(self, value: QByteArray) -> None:
        self._write("MainWindow/log_console_geometry", value)

    def application_icon(self) -> str:
        """Return the selected application icon, falling back to Optical Burst."""
        value = self._read("Appearance/application_icon", None)
        return value if isinstance(value, str) and value in self.APPLICATION_ICON_IDS else "optical_burst"

    def set_application_icon(self, icon_id: str) -> None:
        """Persist a supported application icon identifier."""
        if isinstance(icon_id, str) and icon_id in self.APPLICATION_ICON_IDS:
            self._write("Appearance/application_icon", icon_id)

    def log_console_level(self, fallback: str = "All") -> str:
        value = self._read("MainWindow/log_console_level", fallback)
        # Map preferences saved by the earlier exact-level selector to the new
        # minimum-severity choices. DEBUG meant no filtering, so All is safest.
        migrated = {"DEBUG": "All", "INFO": "INFO+", "WARNING": "WARNING+", "ERROR": "ERROR+"}
        if isinstance(value, str):
            value = migrated.get(value, value)
        return (
            value
            if isinstance(value, str) and value in {"All", "INFO+", "WARNING+", "ERROR+", "CRITICAL"}
            else fallback
        )

    def set_log_console_level(self, level: str) -> None:
        if level in {"All", "INFO+", "WARNING+", "ERROR+", "CRITICAL"}:
            self._write("MainWindow/log_console_level", level)

    def camera_controls_visible(self, camera_id: str, fallback: bool = False) -> bool:
        value = self._read(f"CameraPanels/{camera_id}/controls_visible", None)
        return value if isinstance(value, bool) else fallback

    def set_camera_controls_visible(self, camera_id: str, visible: bool) -> None:
        self._write(f"CameraPanels/{camera_id}/controls_visible", bool(visible))

    def scan_detectors(self, fallback: list[int] | None = None) -> list[int]:
        value = self._read("Scan/detectors", None)
        if not isinstance(value, (list, tuple)):
            return list(fallback or [1])
        ids: list[int] = []
        for item in value:
            detector_id = self._as_int(item)
            if detector_id is not None and detector_id in (1, 2, 3, 4) and detector_id not in ids:
                ids.append(detector_id)
        if 1 not in ids:
            ids.insert(0, 1)
        return sorted(ids)

    def set_scan_detectors(self, detector_ids: list[int]) -> None:
        self._write("Scan/detectors", self.scan_detectors_from_ids(detector_ids))

    @staticmethod
    def scan_detectors_from_ids(detector_ids: list[int]) -> list[int]:
        ids = sorted({value for item in detector_ids if (value := AppSettings._as_int(item)) in (1, 2, 3, 4)})
        if 1 not in ids:
            ids.insert(0, 1)
        return ids

    def directory(self, key: str, fallback: Path | None = None) -> Path:
        default = fallback or Path.cwd()
        if fallback is None and getattr(sys, "frozen", False):
            root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            default = root / "IOPanel" / "output"
            try:
                default.mkdir(parents=True, exist_ok=True)
            except OSError:
                default = Path(tempfile.gettempdir()) / "IOPanel" / "output"
                default.mkdir(parents=True, exist_ok=True)
        value = self._read(key, None)
        if not isinstance(value, str) or not value.strip():
            return default
        try:
            path = Path(value).expanduser()
            return path if path.is_dir() else default
        except (OSError, RuntimeError, ValueError):
            return default

    def set_directory(self, key: str, directory: Path | str) -> None:
        try:
            path = Path(directory).expanduser()
            if path.is_dir():
                self._write(key, str(path.resolve()))
        except (OSError, RuntimeError, ValueError):
            return

    def scan_export_directory(self) -> Path:
        return self.directory("Paths/scan_export")

    def set_scan_export_directory(self, directory: Path | str) -> None:
        self.set_directory("Paths/scan_export", directory)

    def scan_export_formats(self) -> tuple[bool, bool, bool]:
        """Return the preferred CSV, MAT, and FIG export selections."""
        values = tuple(
            self._read(f"Export/formats/{name}", default)
            for name, default in (("csv", True), ("mat", True), ("fig", False))
        )
        defaults = (True, True, False)
        return (
            values[0] if isinstance(values[0], bool) else defaults[0],
            values[1] if isinstance(values[1], bool) else defaults[1],
            values[2] if isinstance(values[2], bool) else defaults[2],
        )

    def set_scan_export_formats(self, *, csv: bool, mat: bool, fig: bool) -> None:
        """Persist format preferences without changing schema-v1 identity."""
        for name, value in (("csv", csv), ("mat", mat), ("fig", fig)):
            self._write(f"Export/formats/{name}", bool(value))

    def power_monitor_export_directory(self) -> Path:
        return self.directory("Paths/power_monitor_export")

    def set_power_monitor_export_directory(self, directory: Path | str) -> None:
        self.set_directory("Paths/power_monitor_export", directory)

    def power_monitor_export_formats(self) -> tuple[bool, bool]:
        values = tuple(self._read(f"Export/power_monitor_formats/{name}", True) for name in ("csv", "mat"))
        return (
            values[0] if isinstance(values[0], bool) else True,
            values[1] if isinstance(values[1], bool) else True,
        )

    def set_power_monitor_export_formats(self, *, csv: bool, mat: bool) -> None:
        self._write("Export/power_monitor_formats/csv", bool(csv))
        self._write("Export/power_monitor_formats/mat", bool(mat))

    def plot_image_directory(self) -> Path:
        return self.directory("Paths/plot_image")

    def set_plot_image_directory(self, directory: Path | str) -> None:
        self.set_directory("Paths/plot_image", directory)

    def camera_screenshot_directory(self, camera_id: str) -> Path:
        return self.directory(f"Paths/camera_screenshot/{camera_id}")

    def set_camera_screenshot_directory(self, camera_id: str, directory: Path | str) -> None:
        self.set_directory(f"Paths/camera_screenshot/{camera_id}", directory)
