"""Stable application-icon identifiers shared by settings and the Qt UI."""

APPLICATION_ICONS = (
    ("optical_burst", "Optical Burst"),
    ("prism_spectrum", "Prism Spectrum"),
)
APPLICATION_ICON_IDS = frozenset(icon_id for icon_id, _name in APPLICATION_ICONS)
DEFAULT_APPLICATION_ICON_ID = "optical_burst"
