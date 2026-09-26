from PySide6.QtCore import Qt

from ui.camera_widgets import ParameterControl


def test_parameter_control_linear_slider(qtbot):
    """Test that the linear slider and edit box stay in sync."""
    widget = ParameterControl(name="Test Linear", min_val=0.0, max_val=100.0, initial_val=50.0, scale="linear")
    qtbot.addWidget(widget)  # Add widget to the test runner

    # Check initial state
    assert widget.slider.value() == 500
    assert widget.edit.text() == "50"

    # Simulate user typing in the line edit
    widget.edit.setText("75")
    qtbot.keyClick(widget.edit, Qt.Key_Enter)

    # Check that the slider updated
    assert widget.slider.value() == 750


def test_parameter_control_linear_zero_minimum_endpoints_and_round_trip(qtbot):
    widget = ParameterControl(name="Zero-based", min_val=0.0, max_val=100.0, initial_val=50.0, scale="linear")
    qtbot.addWidget(widget)

    assert widget.slider.value() == 500
    assert widget.value() == 50.0

    widget.setValue(0.0)
    assert widget.slider.value() == 0
    assert widget.value() == 0.0

    widget.setValue(100.0)
    assert widget.slider.value() == 1000
    assert widget.value() == 100.0

    widget.setValue(75.0)
    assert widget.slider.value() == 750
    assert widget.value() == 75.0
    assert widget.edit.text() == "75"


def test_parameter_control_log_slider_endpoints_and_round_trip(qtbot):
    widget = ParameterControl(name="Log", min_val=10.0, max_val=1000.0, initial_val=100.0, scale="log")
    qtbot.addWidget(widget)

    assert widget.slider.value() == 500
    assert widget.value() == 100.0

    widget.setValue(10.0)
    assert widget.slider.value() == 0
    assert widget.value() == 10.0

    widget.setValue(1000.0)
    assert widget.slider.value() == 1000
    assert widget.value() == 1000.0
