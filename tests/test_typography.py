"""Tests for the bundled application typography."""

from __future__ import annotations

import logging

from PySide6.QtGui import QFont

from config_model import CameraConfig
from ui import typography
from ui.alignment_panel import AlignmentPanel
from ui.camera_widgets import CameraPanel
from ui.plot_widgets import HistogramWidget


def test_bundled_font_resources_register_expected_families_and_preserve_size(qapp):
    initial_size = qapp.font().pointSize()
    sans_family, mono_family = typography.install_application_fonts(qapp)

    assert sans_family == "Geist"
    assert mono_family == "Geist Mono"
    assert qapp.font().family() == sans_family
    assert qapp.font().pointSize() == initial_size


def test_selected_numeric_readouts_use_mono_at_existing_sizes(qapp):
    _sans_family, mono_family = typography.install_application_fonts(qapp)

    histogram = HistogramWidget(None, ["Det 1"])
    assert histogram.text_font.family() == mono_family
    assert histogram.text_font.pointSize() == histogram.value_text_font_size
    histogram.close()

    camera = CameraPanel(None, "Camera", CameraConfig(identifier="sim-1", name="Camera", backend="simulation"))
    assert camera._fps_font.family() == mono_family
    assert camera._fps_font.pointSize() == 10
    assert camera._fps_font.bold()
    camera.close()

    alignment = AlignmentPanel(None, None, None)
    power_font = alignment.power_label.font()
    assert power_font.family() == mono_family
    assert power_font.pointSize() == 20
    assert power_font.bold()
    alignment.close()


def test_font_load_failure_keeps_existing_application_font(qapp, monkeypatch, caplog):
    fallback_font = QFont("Sans Serif", 13)
    qapp.setFont(fallback_font)
    monkeypatch.setattr(typography, "_FONT_FAMILIES", {"sans": None, "mono": None})
    monkeypatch.setattr(typography, "_load_application_font", lambda _path: None)

    with caplog.at_level(logging.WARNING, logger="LabApp.Typography"):
        families = typography.install_application_fonts(qapp)

    assert families == (None, None)
    assert qapp.font().family() == fallback_font.family()
    assert qapp.font().pointSize() == fallback_font.pointSize()
    assert "keeping Qt's existing application font" in caplog.text
