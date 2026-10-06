from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox, QToolButton

from app_settings import AppSettings
from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingSample,
    PowerMonitorRecordingStopReason,
)
from ui import plot_widgets
from ui.main_window import MainWindow
from ui.plot_widgets import PowerMonitorTraceWidget
from ui.power_monitor_export_dialog import PowerMonitorExportRequest
from ui.typography import install_application_fonts


def _settings(detectors=(Detector.DE_1, Detector.DE_3)):
    return PowerMonitorAcquisitionSettings(1550.0, "1.0", "mW", 1.0, LaserInput.LI_1, detectors, 250)


def _recording(settings, elapsed, detector_data):
    now = datetime.now(UTC)
    return PowerMonitorRecording(
        settings=settings,
        elapsed_s=np.asarray(elapsed, dtype=float),
        pout_data=np.arange(len(elapsed), dtype=float),
        detector_data=np.asarray(detector_data, dtype=float).reshape((len(settings.detectors), len(elapsed))),
        detectors=settings.detectors,
        started_at_utc=now,
        completed_at_utc=now + timedelta(seconds=2),
        duration_s=2.0,
        backend="test.Backend",
        simulated=True,
        stop_reason=PowerMonitorRecordingStopReason.USER_STOPPED,
    )


def _app_settings(tmp_path):
    backend = QSettings(str(Path(tmp_path) / "settings.ini"), QSettings.Format.IniFormat)
    backend.clear()
    backend.sync()
    return AppSettings(backend)


def test_trace_uses_shared_power_axis_labels_and_typography(qapp, qtbot, tmp_path):
    install_application_fonts(qapp)
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)

    bottom_axis = widget.plot_widget.getAxis("bottom")
    left_axis = widget.plot_widget.getAxis("left")
    assert bottom_axis.labelText == "Elapsed time (s)"
    assert left_axis.labelText == "Power (dBm)"
    assert bottom_axis.labelStyle["font-size"] == "12pt"
    assert bottom_axis.labelStyle["font-family"] == "Geist"
    assert bottom_axis.labelStyle["font-weight"] == "normal"
    assert bottom_axis.labelStyle["color"] == "black"
    assert left_axis.labelStyle == bottom_axis.labelStyle
    widget.close()


def test_trace_plots_only_selected_detectors_with_actual_times_and_nan_gaps(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.start_recording(_settings())

    assert set(widget.curve_items) == {Detector.DE_1, Detector.DE_3}
    assert widget.plot_widget.getAxis("left").labelText == "Power (dBm)"
    assert widget.plot_widget.getAxis("bottom").labelText == "Elapsed time (s)"
    assert [curve.opts["name"] for curve in widget.curve_items.values()] == ["Det 1", "Det 3"]
    assert [curve.opts["pen"].color().name() for curve in widget.curve_items.values()] == ["#1b9e77", "#7570b3"]

    for elapsed, de1, de3 in ((0.25, 1.0, 3.0), (0.63, 2.0, np.nan), (1.42, 4.0, 6.0)):
        widget.append_sample(PowerMonitorRecordingSample(elapsed, 99.0, (Detector.DE_1, Detector.DE_3), (de1, de3)))

    np.testing.assert_array_equal(widget.elapsed_values, [0.25, 0.63, 1.42])
    np.testing.assert_array_equal(widget.curve_items[Detector.DE_1].getData()[0], [0.25, 0.63, 1.42])
    plotted_de3 = widget.curve_items[Detector.DE_3].getData()[1]
    assert np.isnan(plotted_de3[1])
    np.testing.assert_array_equal(widget.detector_values[Detector.DE_1], [1.0, 2.0, 4.0])
    widget.close()


def test_trace_uses_exact_detector_palette_for_all_optical_channels(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.start_recording(_settings((Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4)))
    assert [widget.curve_items[detector].opts["pen"].color().name() for detector in widget.curve_items] == [
        "#1b9e77",
        "#d95f02",
        "#7570b3",
        "#e7298a",
    ]
    widget.close()


def test_trace_actions_are_compact_overlays_and_status_collapses(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.resize(640, 360)
    widget.show()
    qtbot.waitExposed(widget)

    assert widget.overlay_controls.parentWidget() is widget.plot_container
    assert isinstance(widget.save_recording_button, QToolButton)
    assert isinstance(widget.close_trace_button, QToolButton)
    assert widget.save_recording_button.parentWidget() is widget.overlay_controls
    assert widget.close_trace_button.parentWidget() is widget.overlay_controls
    assert not widget.save_recording_button.icon().isNull()
    assert widget.save_recording_button.toolTip() == "Save Power Monitor recording"
    assert widget.save_recording_button.accessibleName() == "Save Power Monitor recording"
    assert widget.close_trace_button.toolTip() == "Close Power Monitor trace"
    assert widget.close_trace_button.accessibleName() == "Close Power Monitor trace"
    assert widget.save_recording_button.autoRaise()
    assert widget.close_trace_button.autoRaise()
    button_center = widget.close_trace_button.mapTo(widget.plot_container, widget.close_trace_button.rect().center())
    assert button_center.x() > widget.plot_container.width() * 0.8
    assert button_center.y() < 40
    assert widget.plot_widget.geometry().height() == widget.plot_container.height()
    assert not widget.status_label.isVisible()

    widget._set_status("Waiting for recorded samples")
    assert widget.status_label.isVisible()
    assert widget.status_label.text() == "Waiting for recorded samples"
    widget._set_status("")
    assert not widget.status_label.isVisible()
    widget.close()


def test_close_button_hides_only_and_samples_continue(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.show()
    widget.start_recording(_settings())
    first = PowerMonitorRecordingSample(0.25, 8.0, _settings().detectors, (1.0, 3.0))
    widget.append_sample(first)

    button = widget.close_trace_button
    assert isinstance(button, QToolButton)
    assert button.text() == chr(215)
    assert button.toolTip() == "Close Power Monitor trace"
    assert button.width() <= 32 and button.height() <= 32
    button.click()

    assert widget.isHidden()
    assert widget._user_hidden
    assert widget.recording_active
    assert widget.settings == _settings()
    assert widget.elapsed_values == [0.25]
    assert widget.detector_values == {Detector.DE_1: [1.0], Detector.DE_3: [3.0]}
    assert widget.current_recording is None
    assert not widget.save_recording_button.isEnabled()
    np.testing.assert_array_equal(widget.curve_items[Detector.DE_1].getData()[0], [0.25])

    widget.append_sample(PowerMonitorRecordingSample(0.75, 7.0, _settings().detectors, (2.0, 4.0)))
    assert widget.isHidden()
    assert widget.elapsed_values == [0.25, 0.75]
    assert widget.detector_values[Detector.DE_1] == [1.0, 2.0]
    np.testing.assert_array_equal(widget.curve_items[Detector.DE_1].getData()[0], [0.25, 0.75])
    widget.close()


def test_completion_preserves_manual_hide_then_next_start_reopens(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.start_recording(_settings())
    widget.append_sample(PowerMonitorRecordingSample(0.25, 8.0, _settings().detectors, (1.0, 3.0)))
    widget.close_trace_button.click()
    recording = _recording(_settings(), [0.25, 0.7], [[5.0, np.nan], [6.0, 7.0]])

    widget.set_completed_recording(recording)

    assert widget.isHidden()
    assert widget._user_hidden
    assert widget.current_recording is recording
    assert not widget.recording_active
    assert widget.save_recording_button.isEnabled()
    np.testing.assert_array_equal(widget.elapsed_values, recording.elapsed_s)
    assert np.isnan(widget.detector_values[Detector.DE_1][1])
    assert widget.detector_values[Detector.DE_3] == [6.0, 7.0]

    widget.start_recording(_settings((Detector.DE_2,)))
    assert not widget._user_hidden
    assert not widget.isHidden()
    assert widget.current_recording is None
    assert not widget.save_recording_button.isEnabled()
    assert widget.elapsed_values == []
    assert widget.detector_values == {Detector.DE_2: []}
    widget.close()


@pytest.mark.parametrize("manually_hidden", [False, True])
def test_mainwindow_completion_respects_manual_trace_visibility(qtbot, tmp_path, manually_hidden):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.show()
    if manually_hidden:
        widget.close_trace_button.click()
    window_stub = SimpleNamespace(power_monitor_trace_widget=widget)

    MainWindow._handle_power_monitor_recording_completed(
        window_stub,
        _recording(_settings(), [0.25], [[1.0], [3.0]]),
    )

    assert widget.isHidden() is manually_hidden
    assert widget.current_recording is not None


def test_close_after_completion_preserves_recording_and_save_state(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    recording = _recording(_settings(), [0.25], [[1.0], [3.0]])
    widget.set_completed_recording(recording)
    assert widget.save_recording_button.isEnabled()

    widget.close_trace_button.click()

    assert widget.isHidden()
    assert widget._user_hidden
    assert widget.current_recording is recording
    assert widget.save_recording_button.isEnabled()
    assert widget.elapsed_values == [0.25]
    widget.close()


def test_second_recording_resets_data_and_uses_its_frozen_selection(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.start_recording(_settings())
    widget.append_sample(PowerMonitorRecordingSample(0.25, 1.0, (Detector.DE_1, Detector.DE_3), (1.0, 3.0)))

    settings_b = _settings((Detector.DE_2,))
    widget.start_recording(settings_b)

    assert widget.elapsed_values == []
    assert widget.detector_values == {Detector.DE_2: []}
    assert set(widget.curve_items) == {Detector.DE_2}
    assert widget.elapsed_values == []
    widget.close()


def test_completed_zero_sample_and_zero_detector_recordings_canonicalize_and_discard(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    settings = _settings()
    widget.start_recording(settings)
    widget.append_sample(PowerMonitorRecordingSample(0.25, 1.0, settings.detectors, (8.0, 9.0)))

    recording = _recording(settings, [0.25, 0.63], [[2.0, np.nan], [5.0, 6.0]])
    widget.set_completed_recording(recording)
    np.testing.assert_array_equal(widget.elapsed_values, recording.elapsed_s)
    np.testing.assert_array_equal(widget.detector_values[Detector.DE_1], recording.detector_data[0])
    assert not np.isnan(widget.detector_values[Detector.DE_3][0])
    assert np.isnan(widget.detector_values[Detector.DE_1][1])

    empty = _recording(settings, [], [[], []])
    widget.set_completed_recording(empty)
    assert widget.elapsed_values == []
    assert widget.detector_values == {Detector.DE_1: [], Detector.DE_3: []}
    assert widget.status_label.text() == "No samples captured"

    no_detectors = _settings(())
    widget.set_completed_recording(_recording(no_detectors, [], []))
    assert widget.curve_items == {}
    assert widget.status_label.text() == "No detector channels selected"

    widget.start_recording(settings)
    widget.append_sample(PowerMonitorRecordingSample(0.25, 1.0, settings.detectors, (8.0, 9.0)))
    widget.discard_recording()
    assert widget.isHidden()
    assert widget.elapsed_values == []
    assert widget.curve_items == {}
    widget.close()


def test_save_button_tracks_completed_recording_and_invalidates_stale_recordings(qtbot, tmp_path):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    settings = _settings()
    recording_a = _recording(settings, [0.25], [[1.0], [3.0]])

    assert not widget.save_recording_button.isEnabled()
    widget.start_recording(settings)
    assert not widget.save_recording_button.isEnabled()
    widget.set_completed_recording(recording_a)
    assert widget.current_recording is recording_a
    assert widget.save_recording_button.isEnabled()

    recording_b_settings = _settings((Detector.DE_2,))
    widget.start_recording(recording_b_settings)
    assert widget.current_recording is None
    assert not widget.save_recording_button.isEnabled()

    empty = _recording(settings, [], [[], []])
    widget.set_completed_recording(empty)
    assert widget.current_recording is empty
    assert not widget.save_recording_button.isEnabled()
    widget.discard_recording()
    assert widget.current_recording is None
    assert not widget.save_recording_button.isEnabled()
    widget.close()


def test_zero_sample_save_reports_no_samples_without_opening_dialog(qtbot, tmp_path, monkeypatch):
    widget = PowerMonitorTraceWidget(_app_settings(tmp_path))
    qtbot.addWidget(widget)
    widget.set_completed_recording(_recording(_settings(), [], [[], []]))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    monkeypatch.setattr(plot_widgets, "PowerMonitorExportDialog", lambda *_args: pytest.fail("dialog must not open"))

    widget.save_recording()

    assert warnings == [("No Samples", "This recording contains no captured samples and cannot be exported.")]
    assert not widget.save_recording_button.isEnabled()
    widget.close()


class _FakeExportDialog:
    DialogCode = QDialog.DialogCode
    next_result = QDialog.DialogCode.Accepted
    next_request = None
    calls: ClassVar[list[tuple]] = []

    def __init__(self, directory, base_name, formats, parent):
        self.calls.append((directory, base_name, formats, parent))
        self.export_request = self.next_request

    def exec(self):
        return self.next_result


def _export_request(directory, *, csv=True, mat=True, base_name="saved", comment="note"):
    return PowerMonitorExportRequest(directory, base_name, csv, mat, comment)


def test_save_cancel_leaves_preferences_and_files_unchanged(qtbot, tmp_path, monkeypatch):
    settings = _app_settings(tmp_path)
    widget = PowerMonitorTraceWidget(settings)
    qtbot.addWidget(widget)
    recording = _recording(_settings(), [0.25], [[1.0], [3.0]])
    widget.set_completed_recording(recording)
    original_formats = settings.power_monitor_export_formats()
    original_directory = settings.power_monitor_export_directory()
    _FakeExportDialog.next_result = QDialog.DialogCode.Rejected
    _FakeExportDialog.next_request = _export_request(tmp_path, csv=False, mat=True)
    _FakeExportDialog.calls = []
    monkeypatch.setattr(plot_widgets, "PowerMonitorExportDialog", _FakeExportDialog)

    widget.save_recording()

    assert settings.power_monitor_export_formats() == original_formats
    assert settings.power_monitor_export_directory() == original_directory
    assert list(tmp_path.glob("saved.*")) == []
    assert _FakeExportDialog.calls[0][1] == recording.started_at_utc.astimezone(UTC).strftime(
        "power_monitor_%Y%m%dT%H%M%SZ"
    )
    widget.close()


@pytest.mark.parametrize(
    ("csv", "mat", "expected"),
    [(True, False, ["saved.csv"]), (False, True, ["saved.mat"]), (True, True, ["saved.csv", "saved.mat"])],
)
def test_save_writes_only_selected_csv_and_or_mat(qtbot, tmp_path, monkeypatch, csv, mat, expected):
    from scipy.io import loadmat

    settings = _app_settings(tmp_path)
    widget = PowerMonitorTraceWidget(settings)
    qtbot.addWidget(widget)
    widget.set_completed_recording(_recording(_settings(), [0.25, 0.66], [[1.0, 2.0], [3.0, 4.0]]))
    _FakeExportDialog.next_result = QDialog.DialogCode.Accepted
    _FakeExportDialog.next_request = _export_request(tmp_path, csv=csv, mat=mat)
    _FakeExportDialog.calls = []
    monkeypatch.setattr(plot_widgets, "PowerMonitorExportDialog", _FakeExportDialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    widget.save_recording()

    assert sorted(path.name for path in tmp_path.glob("saved.*")) == sorted(expected)
    assert settings.power_monitor_export_directory() == tmp_path.resolve()
    assert settings.power_monitor_export_formats() == (csv, mat)
    if csv:
        csv_text = (tmp_path / "saved.csv").read_text(encoding="utf-8")
        assert "# DetectorUnit: dBm" in csv_text
        assert "# RequestedPollInterval_ms:" in csv_text
        assert "Elapsed_time_[s], Pout_[dBm]" in csv_text
    if mat:
        mat = loadmat(tmp_path / "saved.mat", simplify_cells=True)
        assert mat["comment"] == "note"
        assert mat["pout_unit"] == "dBm"
    widget.close()


def test_overwrite_decline_preflights_only_selected_conflicts_and_keeps_preferences(qtbot, tmp_path, monkeypatch):
    settings = _app_settings(tmp_path)
    widget = PowerMonitorTraceWidget(settings)
    qtbot.addWidget(widget)
    widget.set_completed_recording(_recording(_settings(), [0.25], [[1.0], [3.0]]))
    (tmp_path / "saved.csv").write_text("old", encoding="utf-8")
    _FakeExportDialog.next_result = QDialog.DialogCode.Accepted
    _FakeExportDialog.next_request = _export_request(tmp_path, csv=True, mat=True)
    monkeypatch.setattr(plot_widgets, "PowerMonitorExportDialog", _FakeExportDialog)
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: questions.append(args[2]) or QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        plot_widgets,
        "build_power_monitor_export_v1",
        lambda *_args, **_kwargs: pytest.fail("must preflight before building/writing"),
    )

    widget.save_recording()

    assert len(questions) == 1
    assert "saved.csv" in questions[0]
    assert "saved.mat" not in questions[0]
    assert (tmp_path / "saved.csv").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "saved.mat").exists()
    assert settings.power_monitor_export_formats() == (True, True)
    widget.close()


def test_partial_mat_failure_keeps_csv_and_reports_save_issues(qtbot, tmp_path, monkeypatch):
    import scipy.io

    settings = _app_settings(tmp_path)
    widget = PowerMonitorTraceWidget(settings)
    qtbot.addWidget(widget)
    widget.set_completed_recording(_recording(_settings(), [0.25], [[1.0], [3.0]]))
    _FakeExportDialog.next_result = QDialog.DialogCode.Accepted
    _FakeExportDialog.next_request = _export_request(tmp_path, csv=True, mat=True)
    monkeypatch.setattr(plot_widgets, "PowerMonitorExportDialog", _FakeExportDialog)
    monkeypatch.setattr(scipy.io, "savemat", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("mat failed")))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[1]))

    widget.save_recording()

    assert (tmp_path / "saved.csv").exists()
    assert not (tmp_path / "saved.mat").exists()
    assert warnings == ["Save Issues"]
    widget.close()
