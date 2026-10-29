"""Central, versioned persistence for harmless per-user UI preferences.

Hardware configuration remains in ``config.ini``. Values in this module are
limited to presentation preferences and paths used by file dialogs.
"""

import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

from application_icon_registry import APPLICATION_ICON_IDS as _APPLICATION_ICON_IDS
from application_icon_registry import DEFAULT_APPLICATION_ICON_ID


class AppSettings:
    """Typed boundary around this application's QSettings namespace."""

    SCHEMA_VERSION = 1
    MAX_SPLITTER_SIZE = 1_000_000
    APPLICATION_ICON_IDS = _APPLICATION_ICON_IDS
    # Persist the selected tab by identity so future reordering does not alter
    # the logical page restored from user preferences.
    ACTIVE_TAB_IDS = ("power_monitor", "wavelength_scan", "auto_alignment", "pm5020_monitor", "edfa_monitor")
    LEGACY_TAB_IDS = ("wavelength_scan", "power_monitor", "auto_alignment")
    PM5020_DISPLAY_UNITS = frozenset({"W", "mW", "µW", "nW", "dBm"})
    PM5020_RANGE_UNITS = frozenset({"W", "mW", "µW", "nW"})
    PM5020_HISTORY_SECONDS = (10, 30, 60, 300, 600)

    @staticmethod
    def _finite_number(value: Any, minimum: float, maximum: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            result = float(value)
        except (OverflowError, ValueError):
            return None
        return result if math.isfinite(result) and minimum <= result <= maximum else None

    def pm5020_channel_preferences(self, channel: int) -> dict[str, Any]:
        """Return validated, presentation-only preferences for channel 1 or 2."""
        if isinstance(channel, bool) or channel not in (1, 2):
            raise ValueError("PM5020 channel must be 1 or 2")
        prefix = f"PM5020/Channels/{channel}"
        defaults = {
            "enabled": True,
            "wavelength_nm": 1550.0 if channel == 1 else 775.0,
            "auto_range": True,
            "power_range_w": 1e-3,
            "range_unit": "mW",
            "display_unit": "mW" if channel == 1 else "nW",
            "input_filter_enabled": False,
        }
        bool_fields = ("enabled", "auto_range", "input_filter_enabled")
        for field in bool_fields:
            value = self._read(f"{prefix}/{field}", None)
            if isinstance(value, bool):
                defaults[field] = value
        for field, limits in (("wavelength_nm", (1.0, 100000.0)), ("power_range_w", (1e-15, 1e3))):
            value = self._finite_number(self._read(f"{prefix}/{field}", None), *limits)
            if value is not None:
                defaults[field] = value
        for field, supported in (("range_unit", self.PM5020_RANGE_UNITS), ("display_unit", self.PM5020_DISPLAY_UNITS)):
            value = self._read(f"{prefix}/{field}", None)
            if isinstance(value, str) and value in supported:
                defaults[field] = value
        return defaults

    def set_pm5020_channel_preferences(self, channel: int, values: dict[str, Any]) -> None:
        """Store individually validated preferences; ignore invalid fields."""
        if not self.supported:
            return
        if isinstance(channel, bool) or channel not in (1, 2):
            raise ValueError("PM5020 channel must be 1 or 2")
        prefix = f"PM5020/Channels/{channel}"
        for field in ("enabled", "auto_range", "input_filter_enabled"):
            value = values.get(field)
            if isinstance(value, bool):
                self._write(f"{prefix}/{field}", value)
        for field, limits in (("wavelength_nm", (1.0, 100000.0)), ("power_range_w", (1e-15, 1e3))):
            value = self._finite_number(values.get(field), *limits)
            if value is not None:
                self._write(f"{prefix}/{field}", value)
        for field, supported in (("range_unit", self.PM5020_RANGE_UNITS), ("display_unit", self.PM5020_DISPLAY_UNITS)):
            value = values.get(field)
            if isinstance(value, str) and value in supported:
                self._write(f"{prefix}/{field}", value)

    def pm5020_preferences(self) -> dict[str, Any]:
        interval = self._finite_number(self._read("PM5020/monitor_interval_ms", None), 250, 5000)
        unit = self._read("PM5020/interval_unit", None)
        history = self._as_int(self._read("PM5020/history_seconds", None))
        return {
            "monitor_interval_ms": int(interval) if interval is not None and interval.is_integer() else 1000,
            "interval_unit": unit if isinstance(unit, str) and unit in {"ms", "s"} else "s",
            "history_seconds": history if history in self.PM5020_HISTORY_SECONDS else 10,
        }

    def set_pm5020_preferences(
        self, *, monitor_interval_ms: int | None, interval_unit: str, history_seconds: int
    ) -> None:
        if not self.supported:
            return
        interval = self._finite_number(monitor_interval_ms, 250, 5000) if monitor_interval_ms is not None else None
        if interval is not None and interval.is_integer():
            self._write("PM5020/monitor_interval_ms", int(interval))
        if isinstance(interval_unit, str) and interval_unit in {"ms", "s"}:
            self._write("PM5020/interval_unit", interval_unit)
        history = self._as_int(history_seconds)
        if history in self.PM5020_HISTORY_SECONDS:
            self._write("PM5020/history_seconds", history)

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
        tab_id = self._read("MainWindow/active_tab_id", None)
        if tab_id is not None:
            if isinstance(tab_id, str) and tab_id in self.ACTIVE_TAB_IDS:
                index = self.ACTIVE_TAB_IDS.index(tab_id)
                return index if index < tab_count else fallback
            return fallback

        legacy_index = self._as_int(self._read("MainWindow/active_tab", None))
        if legacy_index is not None and 0 <= legacy_index < len(self.LEGACY_TAB_IDS):
            # Settings written before the tab reorder used scan=0, monitor=1.
            tab_id = self.LEGACY_TAB_IDS[legacy_index]
            index = self.ACTIVE_TAB_IDS.index(tab_id)
            return index if index < tab_count else fallback
        return fallback

    def set_active_tab(self, index: int) -> None:
        self._write("MainWindow/active_tab", int(index))
        if 0 <= index < len(self.ACTIVE_TAB_IDS):
            self._write("MainWindow/active_tab_id", self.ACTIVE_TAB_IDS[index])

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
        return value if isinstance(value, str) and value in self.APPLICATION_ICON_IDS else DEFAULT_APPLICATION_ICON_ID

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

    def pm5020_trace_export_directory(self) -> Path:
        """Keep PM5020 trace export destinations separate from CT400."""
        return self.directory("Paths/pm5020_trace_export")

    def set_pm5020_trace_export_directory(self, directory: Path | str) -> None:
        self.set_directory("Paths/pm5020_trace_export", directory)

    def pm5020_trace_export_formats(self) -> tuple[bool, bool]:
        values = tuple(self._read(f"Export/pm5020_trace_formats/{name}", True) for name in ("csv", "mat"))
        return (
            values[0] if isinstance(values[0], bool) else True,
            values[1] if isinstance(values[1], bool) else True,
        )

    def set_pm5020_trace_export_formats(self, *, csv: bool, mat: bool) -> None:
        self._write("Export/pm5020_trace_formats/csv", bool(csv))
        self._write("Export/pm5020_trace_formats/mat", bool(mat))

    def plot_image_directory(self) -> Path:
        return self.directory("Paths/plot_image")

    def set_plot_image_directory(self, directory: Path | str) -> None:
        self.set_directory("Paths/plot_image", directory)

    def camera_screenshot_directory(self, camera_id: str) -> Path:
        return self.directory(f"Paths/camera_screenshot/{camera_id}")

    def set_camera_screenshot_directory(self, camera_id: str, directory: Path | str) -> None:
        self.set_directory(f"Paths/camera_screenshot/{camera_id}", directory)
