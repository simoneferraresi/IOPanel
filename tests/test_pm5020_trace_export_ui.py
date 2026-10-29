"""PM5020 trace capture is independent of rolling per-channel plots."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QToolButton

from hardware.pm5020_types import PowerChannel, PowerReading
from ui.pm5020_panel import PM5020Panel


def test_channel_history_clear_does_not_discard_exportable_session(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    panel._on_monitoring_started(0, 1)
    panel._on_reading(PowerReading.now(PowerChannel.CH1, 1e-3), 0.12)
    panel._on_reading(PowerReading.now(PowerChannel.CH2, 1e-9), 0.13)
    assert all(not ch.save_traces_button.isEnabled() for ch in (panel.ch1, panel.ch2))
    assert len(panel._trace_samples) == 2

    # Erasing plots changes presentation, not acquired data.
    panel._clear_channel(panel.ch1)
    panel._clear_history()
    assert not panel.ch1._rows and not panel.ch2._rows
    assert len(panel._trace_samples) == 2

    panel._on_activity_state("Connected / idle")
    assert panel._trace_completed_at_utc is not None
    assert all(ch.save_traces_button.isEnabled() for ch in (panel.ch1, panel.ch2))

    # A new acquisition starts a new recording, rather than joining sessions.
    panel._on_monitoring_started(0, 2)
    assert panel._trace_recording_active
    assert len(panel._trace_samples) == 0
    assert all(not ch.save_traces_button.isEnabled() for ch in (panel.ch1, panel.ch2))
    panel._on_activity_state("Connected / idle")
    panel.request_shutdown()
    assert panel.shutdown_finished


def test_single_channel_session_records_only_selected_channel(qtbot):
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    panel.ch2.enabled.setChecked(False)
    panel._on_monitoring_started(0, 1)
    panel._on_reading(PowerReading.now(PowerChannel.CH1, 0.0), 0.5)
    panel._on_activity_state("Connected / idle")
    assert tuple(channel for channel, _ in panel._trace_configs) == (PowerChannel.CH1,)
    assert len(panel._trace_samples) == 1
    assert all(ch.save_traces_button.isEnabled() for ch in (panel.ch1, panel.ch2))
    panel.request_shutdown()


@pytest.mark.parametrize("origin", ("ch1", "ch2"))
def test_save_traces_writes_csv_and_mat_in_background(qtbot, tmp_path, monkeypatch, origin):
    import numpy as np
    from PySide6.QtWidgets import QDialog, QMessageBox

    class Dialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, _directory, _basename, _formats, _parent, *, title):
            assert title == "Save PM5020 Channel Traces"
            self.export_request = type(
                "Request",
                (),
                {
                    "directory": tmp_path,
                    "base_name": "session",
                    "csv": True,
                    "mat": True,
                    "comment": "test export",
                },
            )()

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr("ui.pm5020_panel.PowerMonitorExportDialog", Dialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: QMessageBox.StandardButton.Ok)

    panel = PM5020Panel()
    qtbot.addWidget(panel)
    panel._on_monitoring_started(0, 1)
    panel._on_reading(PowerReading.now(PowerChannel.CH1, 1e-3), 0.25)
    panel._on_reading(PowerReading.now(PowerChannel.CH2, 2e-9), 0.27)
    panel._on_activity_state("Connected / idle")
    getattr(panel, origin).save_traces_button.click()

    qtbot.waitUntil(
        lambda: (
            (tmp_path / "session.csv").is_file()
            and (tmp_path / "session.mat").is_file()
            and not panel._trace_export_lifecycle.busy
        ),
        timeout=10000,
    )
    data = np.loadtxt(tmp_path / "session.csv", delimiter=",")
    assert data.shape == (2, 4)
    assert data[0, 2] == 1e-3 and np.isnan(data[0, 3])
    assert np.isnan(data[1, 2]) and data[1, 3] == 2e-9
    assert "test export" in (tmp_path / "session.csv").read_text(encoding="utf-8")
    from scipy.io import loadmat

    mat = loadmat(tmp_path / "session.mat")
    np.testing.assert_array_equal(mat["sample_channel_id"].ravel(), (1, 2))
    panel.request_shutdown()
    assert panel.shutdown_finished


def test_pm5020_plot_overlays_match_existing_clear_then_save_convention(qtbot):
    """Same save glyph, style and plot-overlay placement as the CT400 tabs."""
    panel = PM5020Panel()
    qtbot.addWidget(panel)
    reference_save_icon = QIcon(":/icons/save.svg")
    assert not reference_save_icon.isNull()

    # The old left-column text button must be gone.
    assert panel.findChild(QPushButton, "pm5020SaveTracesButton") is None

    for channel in (panel.ch1, panel.ch2):
        controls = channel.plot_actions
        toolbar = controls.layout()
        assert [toolbar.itemAt(i).widget() for i in range(toolbar.count())] == [
            channel.clear_button,
            channel.input_filter,
            channel.save_traces_button,
        ]
        # Ensure buttons actually belong to the overlaid plot controls.
        plot_layout = channel.plot_host.layout()
        assert any(plot_layout.itemAt(i).widget() is controls for i in range(plot_layout.count()))
        save_button = channel.save_traces_button
        assert isinstance(save_button, QToolButton)
        assert save_button.objectName() == f"pm5020Ch{channel.channel.value}SaveTracesButton"
        assert save_button.autoRaise()
        assert save_button.size().width() == 28
        assert save_button.size().height() == 28
        assert "CH1 and CH2" in save_button.toolTip()
        assert save_button.icon().pixmap(28, 28).toImage() == reference_save_icon.pixmap(28, 28).toImage()

    panel.request_shutdown()
