"""Central loading and lookup for the supported application icons."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

from resources import resources_rc  # noqa: F401  # Register the compiled Qt resources.

APPLICATION_ICONS = (
    ("optical_burst", "Optical Burst"),
    ("prism_spectrum", "Prism Spectrum"),
)
APPLICATION_ICON_IDS = frozenset(icon_id for icon_id, _name in APPLICATION_ICONS)
APPLICATION_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_ICON_CACHE: dict[str, QIcon] = {}


def application_icon_names() -> tuple[tuple[str, str], ...]:
    """Return supported stable identifiers and their user-facing names."""
    return APPLICATION_ICONS


def application_icon(icon_id: str) -> QIcon:
    """Load and cache a multi-resolution icon, using the legacy icon as fallback."""
    if not isinstance(icon_id, str) or icon_id not in APPLICATION_ICON_IDS:
        icon_id = "optical_burst"
    cached = _ICON_CACHE.get(icon_id)
    if cached is not None:
        return cached

    icon = QIcon()
    for size in APPLICATION_ICON_SIZES:
        icon.addFile(f":/icons/app/{icon_id}/{size}.png", QSize(size, size))
    available = {(size.width(), size.height()) for size in icon.availableSizes()}
    expected = {(size, size) for size in APPLICATION_ICON_SIZES}
    if icon.isNull() or not expected <= available:
        icon = QIcon(":/icons/laser.svg")
    _ICON_CACHE[icon_id] = icon
    return icon
