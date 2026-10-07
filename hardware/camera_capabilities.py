"""Best-effort, read-only GenICam capability inspection and ROI helpers.

Feature access is deliberately isolated from camera startup: optional GenICam
features differ across vendors, firmware revisions, and VmbPy versions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "acquisition_mode": ("AcquisitionMode",),
    "trigger_mode": ("TriggerMode",),
    "pixel_format": ("PixelFormat",),
    "width": ("Width",),
    "height": ("Height",),
    "offset_x": ("OffsetX",),
    "offset_y": ("OffsetY",),
    "width_max": ("WidthMax",),
    "height_max": ("HeightMax",),
    "binning_horizontal": ("BinningHorizontal",),
    "binning_vertical": ("BinningVertical",),
    "frame_rate_enable": ("AcquisitionFrameRateEnable", "AcquisitionFrameRateEnabled"),
    "frame_rate": ("AcquisitionFrameRate", "AcquisitionFrameRateAbs"),
    "exposure": ("ExposureTime", "ExposureTimeAbs"),
    "gain": ("Gain", "GainRaw"),
    "exposure_auto": ("ExposureAuto",),
    "gain_auto": ("GainAuto",),
    "gev_packet_size": ("GevSCPSPacketSize",),
    "gev_packet_delay": ("GevSCPD",),
    "throughput_limit": ("DeviceLinkThroughputLimit", "StreamBytesPerSecond"),
}


@dataclass(frozen=True)
class FeatureCapability:
    name: str
    available: bool
    readable: bool
    writable: bool
    value_type: str | None = None
    value: Any = None
    minimum: Any = None
    maximum: Any = None
    increment: Any = None
    unit: str | None = None
    values: tuple[str, ...] = ()
    error: str | None = None


def _safe_call(obj: Any, method: str, default: Any = None) -> Any:
    try:
        fn = getattr(obj, method, None)
        return fn() if callable(fn) else default
    except Exception:  # noqa: BLE001 - vendor feature APIs expose different read errors.
        return default


def find_feature(device: Any, aliases: tuple[str, ...] | list[str]) -> tuple[str | None, Any | None]:
    """Return the first named feature that the device exposes."""
    for name in aliases:
        try:
            return name, device.get_feature_by_name(name)
        except Exception:  # noqa: BLE001, S112 - try the next supported feature alias.
            continue
    return None, None


def inspect_feature(device: Any, aliases: tuple[str, ...] | list[str]) -> FeatureCapability:
    """Inspect one feature without letting optional-feature errors escape."""
    name, feature = find_feature(device, aliases)
    if feature is None or name is None:
        return FeatureCapability(name=aliases[0], available=False, readable=False, writable=False)

    readable = bool(_safe_call(feature, "is_readable", True))
    writable = bool(_safe_call(feature, "is_writeable", False))
    value = _safe_call(feature, "get") if readable else None
    errors = []
    bounds = None
    if readable:
        try:
            bounds = feature.get_range()
        except Exception as exc:  # noqa: BLE001 - numeric ranges vary across GenICam feature types.
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                errors.append(f"range unreadable: {exc}")
    minimum, maximum = bounds if isinstance(bounds, (tuple, list)) and len(bounds) == 2 else (None, None)
    increment = _safe_call(feature, "get_increment")
    unit = _safe_call(feature, "get_unit")
    entries = _safe_call(feature, "get_available_entries")
    if entries is None:
        entries = _safe_call(feature, "get_available_values")
    values = tuple(str(_safe_call(entry, "get_name", entry)) for entry in (entries or ()))
    if readable and value is None:
        errors.append("value unreadable")
    if readable and isinstance(value, (int, float)) and not isinstance(value, bool) and bounds is None and not errors:
        errors.append("numeric range unavailable")
    return FeatureCapability(
        name=name,
        available=True,
        readable=readable and value is not None,
        writable=writable,
        value_type=(
            "enum"
            if values
            else "boolean"
            if isinstance(value, bool)
            else "integer"
            if isinstance(value, int)
            else "float"
            if isinstance(value, float)
            else type(value).__name__
            if value is not None
            else None
        ),
        value=value,
        minimum=minimum,
        maximum=maximum,
        increment=increment,
        unit=str(unit) if unit is not None else None,
        values=values,
        error="; ".join(errors) or None,
    )


def inspect_camera(device: Any) -> dict[str, Any]:
    """Build a JSON-serializable, read-only camera capability report."""
    features = {key: asdict(inspect_feature(device, aliases)) for key, aliases in FEATURE_ALIASES.items()}
    pixel_formats = _safe_call(device, "get_pixel_formats", ()) or ()
    report: dict[str, Any] = {
        "identity": {
            "id": _safe_call(device, "get_id"),
            "name": _safe_call(device, "get_name"),
            "model": _safe_call(device, "get_model"),
            "serial": _safe_call(device, "get_serial"),
            "interface_id": _safe_call(device, "get_interface_id"),
            "transport_layer": _safe_call(device, "get_transport_layer", None),
        },
        "features": features,
        "supported_pixel_formats": [str(getattr(fmt, "name", fmt)) for fmt in pixel_formats],
        "notes": ["Inspection is read-only; acquisition is not started."],
    }
    return report


@dataclass(frozen=True)
class ROI:
    width: int
    height: int
    offset_x: int = 0
    offset_y: int = 0


def _aligned(value: int, minimum: int | None, increment: int | None) -> bool:
    if minimum is None or not increment:
        return True
    return (value - minimum) % int(increment) == 0


def validate_roi(roi: ROI, capabilities: dict[str, FeatureCapability]) -> None:
    """Validate requested geometry against queried bounds and increments."""
    for key, value in (
        ("width", roi.width),
        ("height", roi.height),
        ("offset_x", roi.offset_x),
        ("offset_y", roi.offset_y),
    ):
        cap = capabilities[key]
        if not cap.available or not cap.writable:
            raise ValueError(f"ROI feature {cap.name} is unavailable or read-only")
        if isinstance(cap.minimum, (int, float)) and value < cap.minimum:
            raise ValueError(f"{cap.name} {value} is below minimum {cap.minimum}")
        if isinstance(cap.maximum, (int, float)) and value > cap.maximum:
            raise ValueError(f"{cap.name} {value} is above maximum {cap.maximum}")
        inc = int(cap.increment) if isinstance(cap.increment, (int, float)) and cap.increment else None
        if not _aligned(value, int(cap.minimum) if isinstance(cap.minimum, (int, float)) else None, inc):
            raise ValueError(f"{cap.name} {value} does not match increment {cap.increment}")
    for dim, offset, max_name in ((roi.width, roi.offset_x, "width_max"), (roi.height, roi.offset_y, "height_max")):
        maximum = capabilities[max_name].value
        if isinstance(maximum, (int, float)) and dim + offset > maximum:
            raise ValueError(f"ROI extent {dim + offset} exceeds {max_name}={maximum}")


def read_roi(device: Any) -> ROI:
    values = []
    for field in ("width", "height", "offset_x", "offset_y"):
        cap = inspect_feature(device, FEATURE_ALIASES[field])
        if not cap.readable:
            raise RuntimeError(f"Cannot read ROI feature {cap.name}")
        values.append(int(cap.value))
    return ROI(*values)


def apply_roi(device: Any, roi: ROI) -> ROI:
    """Apply an explicitly requested ROI and attempt to restore on failure.

    Offsets are reset before dimension changes, a broadly compatible GenICam
    sequence that makes room for both shrinking and enlarging the image.
    The final values are always read back and checked.
    """
    original = read_roi(device)
    caps = {
        key: inspect_feature(device, FEATURE_ALIASES[key])
        for key in ("width", "height", "offset_x", "offset_y", "width_max", "height_max")
    }

    def use_sensor_maxima(feature_caps: dict[str, FeatureCapability]) -> None:
        for dimension, maximum in (("width", "width_max"), ("height", "height_max")):
            maximum_value = feature_caps[maximum].value
            if isinstance(maximum_value, (int, float)):
                feature_caps[dimension] = replace(feature_caps[dimension], maximum=maximum_value)

    use_sensor_maxima(caps)
    reset_x = int(caps["offset_x"].minimum or 0)
    reset_y = int(caps["offset_y"].minimum or 0)
    validate_roi(ROI(roi.width, roi.height, reset_x, reset_y), caps)

    def write(candidate: ROI) -> None:
        for key, value in (
            ("offset_x", reset_x),
            ("offset_y", reset_y),
            ("width", candidate.width),
            ("height", candidate.height),
        ):
            _, feat = find_feature(device, FEATURE_ALIASES[key])
            if feat is None:
                raise RuntimeError(f"ROI feature {key} disappeared")
            feat.set(value)
        refreshed = {
            key: inspect_feature(device, FEATURE_ALIASES[key])
            for key in ("width", "height", "offset_x", "offset_y", "width_max", "height_max")
        }
        use_sensor_maxima(refreshed)
        validate_roi(candidate, refreshed)
        for key, value in (("offset_x", candidate.offset_x), ("offset_y", candidate.offset_y)):
            _, feat = find_feature(device, FEATURE_ALIASES[key])
            if feat is None:
                raise RuntimeError(f"ROI feature {key} disappeared")
            feat.set(value)
        actual = read_roi(device)
        if actual != candidate:
            raise RuntimeError(f"ROI readback {actual} does not match requested {candidate}")

    try:
        write(roi)
    except Exception:
        try:
            write(original)
        except Exception as restore_error:
            raise RuntimeError(
                f"ROI update failed and original ROI restoration failed: {restore_error}"
            ) from restore_error
        raise
    return original


def centered_offset(cap: FeatureCapability, sensor_extent: int, roi_extent: int) -> int:
    """Return the closest centered offset aligned to the feature increment."""
    if roi_extent > sensor_extent:
        raise ValueError("ROI extent cannot exceed sensor extent")
    minimum = int(cap.minimum or 0)
    inc = max(1, int(cap.increment or 1))
    raw = max(minimum, (sensor_extent - roi_extent) // 2)
    return minimum + round((raw - minimum) / inc) * inc


def capability_dict(device: Any) -> dict[str, Any]:
    """Alias retained for diagnostic callers."""
    return inspect_camera(device)
