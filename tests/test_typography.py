"""Tests for the bundled application typography."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout

from config_model import CameraConfig
from ui import typography
from ui.alignment_panel import AlignmentPanel
from ui.camera_widgets import CameraPanel
from ui.plot_widgets import HistogramWidget


@pytest.fixture
def preserved_application_font(qapp):
    original_font = QFont(qapp.font())
    try:
        yield qapp
    finally:
        qapp.setFont(original_font)


@pytest.mark.parametrize(("incoming_size", "expected_size"), [(9.0, 10.0), (10.0, 10.0), (11.0, 11.0)])
def test_bundled_font_resources_enforce_minimum_baseline(preserved_application_font, incoming_size, expected_size):
    qapp = preserved_application_font
    incoming_font = QFont("Sans Serif")
    incoming_font.setPointSizeF(incoming_size)
    qapp.setFont(incoming_font)
    sans_family, mono_family = typography.install_application_fonts(qapp)

    assert sans_family == "Geist"
    assert mono_family == "Geist Mono"
    assert qapp.font().family() == sans_family
    assert qapp.font().pointSizeF() == expected_size


def test_group_box_title_style_does_not_bold_or_resize_child(preserved_application_font):
    qapp = preserved_application_font
    typography.install_application_fonts(qapp)
    group = QGroupBox("Section")
    child = QLabel("Normal child", group)
    layout = QVBoxLayout(group)
    layout.addWidget(child)
    typography.style_group_box_title(group)

    assert "QGroupBox::title" in group.styleSheet()
    assert "font-weight: bold" in group.styleSheet()
    assert not child.font().bold()
    assert child.font().family() == qapp.font().family()
    assert child.font().pointSizeF() == qapp.font().pointSizeF()
    group.close()


def test_pixel_sized_incoming_font_uses_ten_point_fallback(preserved_application_font):
    qapp = preserved_application_font
    pixel_font = QFont("Sans Serif")
    pixel_font.setPixelSize(14)
    qapp.setFont(pixel_font)

    typography.install_application_fonts(qapp)

    assert qapp.font().pointSizeF() == 10.0


def test_selected_numeric_readouts_use_mono_at_existing_sizes(preserved_application_font):
    qapp = preserved_application_font
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
    for group_box in (alignment.laser_group, alignment.align_group, alignment.map_group, alignment.power_group):
        assert "QGroupBox::title" in group_box.styleSheet()
        assert "font-weight: bold" in group_box.styleSheet()
    assert not alignment.wavelength_input.font().bold()
    assert alignment.wavelength_input.font().family() == qapp.font().family()
    assert alignment.wavelength_input.font().pointSizeF() == qapp.font().pointSizeF()
    power_font = alignment.power_label.font()
    assert power_font.family() == mono_family
    assert power_font.pointSize() == 20
    assert power_font.bold()
    alignment.close()


def test_font_load_failure_keeps_existing_application_font(preserved_application_font, monkeypatch, caplog):
    qapp = preserved_application_font
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
