import io
from datetime import UTC, datetime

import numpy as np
import pytest
from scipy.io import savemat

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_import import ImportedScan, ScanImportError, load_scan
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui.control_panel import ScanSettings
from ui.plot_widgets import PlotWidget


def _measurement():
    detectors = (Detector.DE_3, Detector.DE_1)
    settings = ScanAcquisitionSettings(
        requested_start_wavelength_nm=1500,
        requested_end_wavelength_nm=1502,
        requested_resolution_pm=20,
        requested_speed_nm_s="5",
        entered_laser_power="2.5",
        entered_laser_power_unit="mW",
        laser_power_mw=2.5,
        laser_input=LaserInput.LI_2,
        detectors=detectors,
    )
    return ScanMeasurement(
        settings=settings,
        wavelengths_nm=np.array([1500.123456, 1501.0, 1502.0]),
        detector_data=np.array([[3.25, np.nan, np.inf], [-1.5, 2.0, -np.inf]]),
        detectors=detectors,
        final_pout=None,
        result_kind=CT400ScanResultKind.SUCCESS,
        raw_result_code=0,
        result_message="not exported",
        backend="fixture.backend",
        simulated=True,
        completed_at_utc=datetime(2026, 10, 1, 12, 30, tzinfo=UTC),
    )


def _write_csv(path, payload):
    buffer = io.StringIO()
    np.savetxt(buffer, payload.csv_data, delimiter=",", header=payload.csv_header, comments="", fmt="%.6f")
    path.write_text(buffer.getvalue(), encoding="utf-8")


@pytest.mark.parametrize("format", ["csv", "mat"])
def test_schema_v2_export_import_round_trip_preserves_order_values_and_metadata(tmp_path, format):
    payload = build_scan_export_v2(_measurement(), comment="café, 測定\nsecond line")
    path = tmp_path / f"scan.{format}"
    if format == "csv":
        _write_csv(path, payload)
    else:
        savemat(path, payload.mat_data, do_compression=True)

    scan = load_scan(path)

    assert isinstance(scan, ImportedScan)
    assert scan.detectors == (Detector.DE_3, Detector.DE_1)
    assert scan.source_path == path.resolve()
    assert scan.source_format == format.upper()
    assert scan.completed_at_utc == datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    assert scan.resolution_pm == 20
    assert scan.speed_nm_s == 5
    assert scan.laser_power == 2.5
    assert scan.laser_power_unit == "mW"
    assert scan.laser_input is LaserInput.LI_2
    assert scan.comment == "café, 測定\nsecond line"
    assert scan.backend == "fixture.backend"
    assert scan.simulated is True
    assert scan.result_kind is CT400ScanResultKind.SUCCESS
    assert scan.raw_result_code == 0
    assert scan.final_pout is None
    assert not scan.wavelengths_nm.flags.writeable
    assert not scan.detector_data.flags.writeable
    if format == "csv":
        np.testing.assert_allclose(scan.wavelengths_nm, [1500.123456, 1501, 1502])
        assert np.isnan(scan.detector_data[0, 1])
        assert np.isposinf(scan.detector_data[0, 2])
        assert np.isneginf(scan.detector_data[1, 2])
    else:
        np.testing.assert_equal(scan.wavelengths_nm, _measurement().wavelengths_nm)
        np.testing.assert_equal(scan.detector_data, _measurement().detector_data)


def test_csv_ignores_zero_filled_inactive_detector_columns(tmp_path):
    payload = build_scan_export_v2(_measurement())
    path = tmp_path / "scan.csv"
    _write_csv(path, payload)
    scan = load_scan(path)
    assert scan.detectors == (Detector.DE_3, Detector.DE_1)
    assert scan.detector_data.shape == (2, 3)


def test_imported_plot_uses_detector_traces_reference_and_disables_reexport(tmp_path, qtbot):
    payload = build_scan_export_v2(_measurement(), comment="offline")
    path = tmp_path / "saved.csv"
    _write_csv(path, payload)
    scan = load_scan(path)
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)

    assert widget.set_imported_scan(scan)
    assert widget.current_imported_scan is scan
    assert not widget.save_btn.isEnabled()
    assert widget.plot_widget.getPlotItem().titleLabel.text == f"Imported Scan — {path.name} (simulated acquisition)"
    assert widget.detector_plot_items[Detector.DE_3].getData()[1].tolist() == [3.25]
    assert np.isposinf(scan.detector_data[0, 2])
    widget.freeze_current_trace()
    assert widget.reference_measurement is scan
    assert widget.reference_detector_plot_items[Detector.DE_3].getData()[1].tolist() == [3.25]

    widget.close()


def test_late_fig_completion_keeps_imported_plot_and_save_state(tmp_path, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from ui import plot_widgets
    from ui.scan_export_dialog import ScanExportRequest

    payload = build_scan_export_v2(_measurement())
    csv_path = tmp_path / "saved.csv"
    _write_csv(csv_path, payload)
    imported = load_scan(csv_path)
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    original = _measurement()
    widget.set_measurement(original)
    widget.freeze_current_trace()
    frozen = widget.reference_measurement

    class FigDialog:
        DialogCode = QDialog.DialogCode

        export_request = ScanExportRequest(tmp_path, "pending", False, False, True, True, "")

        def __init__(self, *_args):
            pass

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(plot_widgets, "ScanExportDialog", FigDialog)
    monkeypatch.setattr(widget, "_queue_matlab_fig", lambda _request: setattr(widget, "pending_saves", 1))
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)
    widget.save_scan_data()
    assert widget.pending_saves == 1
    assert widget.has_unsaved_acquisition()

    widget.set_imported_scan(imported)
    assert not widget.save_btn.isEnabled()
    widget._handle_matlab_save_finished("fig", True, str(tmp_path / "pending.fig"))

    assert widget.current_imported_scan is imported
    assert widget.reference_measurement is frozen
    assert not widget.save_btn.isEnabled()
    assert not widget.has_unsaved_acquisition()
    widget.close()


@pytest.mark.parametrize(
    ("replace", "message"),
    [
        ("# SchemaVersion: 9", "Unsupported scan schema version"),
        ("# ActiveDetectors: ", "Active detector count"),
        ("# OpticalDetectorMask(DE1-DE4): 1,0,0,0", "mask is inconsistent"),
        ("# OpticalDetectorMask(DE1-DE4): 1,2,1,0", "mask must contain four 0/1 values"),
        ("# CompletedAtUTC: yesterday", "valid ISO-8601"),
        ("# ActiveDetectorCount: 0", "Active detector count"),
        ("# ActiveDetectors: DE_3,DE_3", "contain duplicates"),
    ],
)
def test_csv_rejects_invalid_metadata(tmp_path, replace, message):
    payload = build_scan_export_v2(_measurement())
    text = (
        payload.csv_header.replace("# SchemaVersion: 2", replace) if "SchemaVersion" in replace else payload.csv_header
    )
    if replace.startswith("# ActiveDetectors"):
        text = text.replace("# ActiveDetectors: DE_3,DE_1", replace)
    elif replace.startswith("# OpticalDetectorMask"):
        text = text.replace("# OpticalDetectorMask(DE1-DE4): 1,0,1,0", replace)
    elif replace.startswith("# CompletedAtUTC"):
        text = text.replace("# CompletedAtUTC: 2026-10-01T12:30:00Z", replace)
    elif replace.startswith("# ActiveDetectorCount"):
        text = text.replace("# ActiveDetectorCount: 2", replace)
    path = tmp_path / "bad.csv"
    path.write_text(text + "\n" + "\n".join(",".join(map(str, row)) for row in payload.csv_data), encoding="utf-8")
    with pytest.raises(ScanImportError, match=message):
        load_scan(path)


def test_csv_rejects_truncated_rows_and_invalid_numbers(tmp_path):
    payload = build_scan_export_v2(_measurement())
    path = tmp_path / "truncated.csv"
    _write_csv(path, payload)
    path.write_text(path.read_text(encoding="utf-8").replace("1500.123456,", "bad,"), encoding="utf-8")
    with pytest.raises(ScanImportError, match="invalid numeric"):
        load_scan(path)


def test_csv_rejects_missing_metadata_and_wavelength_column(tmp_path):
    payload = build_scan_export_v2(_measurement())
    path = tmp_path / "missing.csv"
    header = payload.csv_header.replace("# Backend: fixture.backend\n", "")
    path.write_text(header + "\n" + "\n".join(",".join(map(str, row)) for row in payload.csv_data), encoding="utf-8")
    with pytest.raises(ScanImportError, match="Missing required metadata: Backend"):
        load_scan(path)
    path.write_text(payload.csv_header.replace("WL_[nm]", "wavelength") + "\n", encoding="utf-8")
    with pytest.raises(ScanImportError, match="wavelength data column"):
        load_scan(path)


def test_mat_rejects_ambiguous_dimensions_and_corrupt_files(tmp_path):
    payload = build_scan_export_v2(_measurement())
    malformed = dict(payload.mat_data)
    malformed["wl_nm"] = np.ones((2, 2))
    path = tmp_path / "bad.mat"
    savemat(path, malformed)
    with pytest.raises(ScanImportError, match="must be a vector"):
        load_scan(path)
    path.write_bytes(b"not a MAT file")
    with pytest.raises(ScanImportError):
        load_scan(path)


def test_mat_rejects_mismatched_sample_lengths(tmp_path):
    malformed = dict(build_scan_export_v2(_measurement()).mat_data)
    malformed["transfer_function_det_3_dB"] = np.array([1.0, 2.0])
    path = tmp_path / "length-mismatch.mat"
    savemat(path, malformed)
    with pytest.raises(ScanImportError, match="exactly one sample per wavelength"):
        load_scan(path)


@pytest.mark.parametrize("contents,suffix", [(b"", ".csv"), (b"x", ".txt"), (b"bad", ".mat")])
def test_import_rejects_empty_or_unsupported_inputs(tmp_path, contents, suffix):
    path = tmp_path / f"input{suffix}"
    path.write_bytes(contents)
    with pytest.raises(ScanImportError):
        load_scan(path)


def test_import_rejects_excessive_input_size(tmp_path, monkeypatch):
    from logic import scan_import

    path = tmp_path / "large.csv"
    path.write_bytes(b"0123456789")
    monkeypatch.setattr(scan_import, "MAX_SCAN_FILE_BYTES", 5)
    with pytest.raises(ScanImportError, match="exceeds the 32 MiB import limit"):
        load_scan(path)
