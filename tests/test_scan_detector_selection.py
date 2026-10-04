import threading

import numpy as np
import pytest

from config_model import AppConfig
from hardware.ct400_types import Detector, Enable
from hardware.dummy_ct400 import DummyCT400
from ui.control_panel import CT400ControlPanel, HistogramControlPanel, ScanSettings
from ui.plot_widgets import PlotWidget


@pytest.fixture
def scan_panel(qtbot):
    device = DummyCT400(scan_duration=0)
    panel = CT400ControlPanel(ScanSettings(), device, AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(panel)
    panel.initial_wl.setText("1550.0")
    panel.final_wl.setText("1550.004")
    panel.resolution.setText("1")
    panel.motor_speed.setText("7")
    panel.laser_power.setText("3")
    return panel


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ((), (Detector.DE_1,)),
        ((Detector.DE_3,), (Detector.DE_1, Detector.DE_3)),
        ((Detector.DE_2, Detector.DE_4), (Detector.DE_1, Detector.DE_2, Detector.DE_4)),
        (
            (Detector.DE_2, Detector.DE_3, Detector.DE_4),
            (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4),
        ),
    ],
)
def test_scan_detector_selection_is_identity_based_ordered_and_excludes_de5(scan_panel, selected, expected):
    for detector in (Detector.DE_2, Detector.DE_3, Detector.DE_4):
        scan_panel.scan_detector_cbs[detector].setChecked(detector in selected)

    assert scan_panel._selected_scan_detectors() == expected
    assert Detector.DE_5 not in scan_panel._selected_scan_detectors()


def test_scan_detector_controls_have_required_defaults_and_connection_states(scan_panel):
    assert tuple(scan_panel.scan_detector_cbs) == (
        Detector.DE_1,
        Detector.DE_2,
        Detector.DE_3,
        Detector.DE_4,
    )
    assert [checkbox.text() for checkbox in scan_panel.scan_detector_cbs.values()] == [
        "Det 1",
        "Det 2",
        "Det 3",
        "Det 4",
    ]
    assert [checkbox.isChecked() for checkbox in scan_panel.scan_detector_cbs.values()] == [True, False, False, False]
    assert [checkbox.objectName() for checkbox in scan_panel.scan_detector_cbs.values()] == [
        "scanDetector1CheckBox",
        "scanDetector2CheckBox",
        "scanDetector3CheckBox",
        "scanDetector4CheckBox",
    ]
    assert scan_panel.scan_detector_cbs[Detector.DE_1].toolTip() == "Detector 1 is always enabled by the CT400."
    assert not scan_panel.scan_detector_cbs[Detector.DE_1].isEnabled()
    optional_detectors = (Detector.DE_2, Detector.DE_3, Detector.DE_4)
    assert all(scan_panel.scan_detector_cbs[detector].isEnabled() for detector in optional_detectors)

    scan_panel.on_instrument_connected(False)
    assert all(not scan_panel.scan_detector_cbs[detector].isEnabled() for detector in scan_panel.scan_detector_cbs)
    assert scan_panel.scan_detector_cbs[Detector.DE_1].isChecked()
    scan_panel.on_instrument_connected(True)
    assert not scan_panel.scan_detector_cbs[Detector.DE_1].isEnabled()
    assert scan_panel.scan_detector_cbs[Detector.DE_1].isChecked()
    assert all(scan_panel.scan_detector_cbs[detector].isEnabled() for detector in optional_detectors)

    scan_panel.set_ct400_operation_state("MONITORING")
    assert all(not scan_panel.scan_detector_cbs[detector].isEnabled() for detector in optional_detectors)
    assert not scan_panel.scan_detector_cbs[Detector.DE_1].isEnabled()
    scan_panel.set_ct400_operation_state("IDLE")
    assert all(scan_panel.scan_detector_cbs[detector].isEnabled() for detector in optional_detectors)


def test_scan_and_monitor_detector_preferences_are_independent(qtbot, scan_panel):
    monitor = HistogramControlPanel(None, AppConfig())
    qtbot.addWidget(monitor)
    try:
        scan_panel.scan_detector_cbs[Detector.DE_3].setChecked(True)
        assert [checkbox.isChecked() for checkbox in monitor.detector_cbs] == [True, True, True, True]

        monitor.detector_cbs[1].setChecked(False)
        assert scan_panel.scan_detector_cbs[Detector.DE_2].isChecked() is False
        assert scan_panel.scan_detector_cbs[Detector.DE_3].isChecked() is True
    finally:
        monitor.cleanup_worker_thread()


def test_simulated_scan_freezes_detector_selection_and_applies_one_array_write(qtbot, scan_panel):
    device = scan_panel.ct400
    assert isinstance(device, DummyCT400)
    gate = threading.Event()
    device._wait_gate = gate

    detector_array_writes = []
    data_requests = []
    original_get_data_points = device.get_data_points
    device.set_detector_array = lambda *flags: detector_array_writes.append(flags)

    def record_data_request(detectors):
        data_requests.append(tuple(detectors))
        return original_get_data_points(detectors)

    device.get_data_points = record_data_request
    measurements = []
    scan_panel.scan_data_ready.connect(measurements.append)

    # Idle checkbox edits only configure the next scan; they do not touch hardware.
    for detector in (Detector.DE_2, Detector.DE_3, Detector.DE_4):
        scan_panel.scan_detector_cbs[detector].setChecked(True)
    scan_panel.scan_detector_cbs[Detector.DE_2].setChecked(False)
    scan_panel.scan_detector_cbs[Detector.DE_4].setChecked(False)
    assert detector_array_writes == []

    try:
        scan_panel._start_scan()
        qtbot.waitUntil(lambda: len(detector_array_writes) == 1, timeout=2000)
        assert detector_array_writes == [(Enable.DISABLE, Enable.ENABLE, Enable.DISABLE, Enable.DISABLE)]
        assert all(
            not scan_panel.scan_detector_cbs[detector].isEnabled()
            for detector in (Detector.DE_2, Detector.DE_3, Detector.DE_4)
        )
        assert scan_panel.scan_detector_cbs[Detector.DE_1].isChecked()
        assert not scan_panel.scan_detector_cbs[Detector.DE_1].isEnabled()

        # Programmatic state changes during the worker cannot alter its frozen selection.
        scan_panel.scan_detector_cbs[Detector.DE_2].setChecked(True)
        scan_panel.scan_detector_cbs[Detector.DE_3].setChecked(False)
        gate.set()
        qtbot.waitUntil(lambda: len(measurements) == 1, timeout=3000)
        qtbot.waitUntil(lambda: not scan_panel.scanning, timeout=2000)

        first = measurements[0]
        assert first.settings.detectors == (Detector.DE_1, Detector.DE_3)
        assert first.detectors == first.settings.detectors
        assert first.detector_data.shape == (2, len(first.wavelengths_nm))
        assert data_requests == [(Detector.DE_1, Detector.DE_3)]
        assert len(detector_array_writes) == 1
        assert scan_panel.scan_detector_cbs[Detector.DE_1].isChecked()
        assert not scan_panel.scan_detector_cbs[Detector.DE_1].isEnabled()
        assert all(
            scan_panel.scan_detector_cbs[detector].isEnabled()
            for detector in (Detector.DE_2, Detector.DE_3, Detector.DE_4)
        )

        plot = PlotWidget(ScanSettings())
        qtbot.addWidget(plot)
        assert plot.set_measurement(first)
        for detector in (Detector.DE_1, Detector.DE_3):
            plotted_x, plotted_y = plot.detector_plot_items[detector].getData()
            np.testing.assert_array_equal(plotted_x, first.wavelengths_nm)
            np.testing.assert_array_equal(plotted_y, first.detector_data[first.detectors.index(detector)])

        # A non-adjacent selection proves the UI preserves detector identity, not count.
        gate.clear()
        scan_panel.scan_detector_cbs[Detector.DE_2].setChecked(True)
        scan_panel.scan_detector_cbs[Detector.DE_3].setChecked(False)
        scan_panel.scan_detector_cbs[Detector.DE_4].setChecked(True)
        scan_panel._start_scan()
        qtbot.waitUntil(lambda: len(detector_array_writes) == 2, timeout=2000)
        assert detector_array_writes[1] == (Enable.ENABLE, Enable.DISABLE, Enable.ENABLE, Enable.DISABLE)
        gate.set()
        qtbot.waitUntil(lambda: len(measurements) == 2, timeout=3000)
        qtbot.waitUntil(lambda: not scan_panel.scanning, timeout=2000)

        second = measurements[1]
        assert second.settings.detectors == (Detector.DE_1, Detector.DE_2, Detector.DE_4)
        assert second.detectors == second.settings.detectors
        assert data_requests[1] == (Detector.DE_1, Detector.DE_2, Detector.DE_4)
        assert len(detector_array_writes) == 2
    finally:
        gate.set()
        if scan_panel.scanning and scan_panel.scan_thread is not None:
            qtbot.waitUntil(lambda: not scan_panel.scanning, timeout=3000)
