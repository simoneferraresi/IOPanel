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
