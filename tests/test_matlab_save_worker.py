import json
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QSignalSpy

from ui import plot_widgets
from ui.control_panel import ScanSettings
from ui.plot_widgets import MatlabSaveWorker, PlotWidget


class _FakeMatlabEngine:
    def __init__(self, *, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name == self.fail_on:
            raise RuntimeError(f"{name} failed")

    def figure(self, **kwargs):
        self._record("figure", **kwargs)
        return "worker-figure"

    def plot(self, *args, **kwargs):
        self._record("plot", *args, **kwargs)

    def hold(self, *args, **kwargs):
        self._record("hold", *args, **kwargs)

    def xlabel(self, *args, **kwargs):
        self._record("xlabel", *args, **kwargs)

    def ylabel(self, *args, **kwargs):
        self._record("ylabel", *args, **kwargs)

    def title(self, *args, **kwargs):
        self._record("title", *args, **kwargs)

    def grid(self, *args, **kwargs):
        self._record("grid", *args, **kwargs)

    def legend(self, **kwargs):
        self._record("legend", **kwargs)

    def savefig(self, *args, **kwargs):
        self._record("savefig", *args, **kwargs)

    def close(self, *args, **kwargs):
        self._record("close", *args, **kwargs)

    def quit(self):
        self._record("quit")


class _MatlabArraySentinel:
    def __init__(self, values):
        self.values = list(values)


def _matlab_values(value):
    return getattr(value, "values", None)


def _setup_worker(monkeypatch, engine):
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(double=_MatlabArraySentinel, engine=SimpleNamespace()),
        raising=False,
    )
    widget = PlotWidget(ScanSettings())
    widget.get_matlab_engine = lambda: engine
    worker = MatlabSaveWorker()
    return widget, worker


def _call(worker, widget, payload):
    spy = QSignalSpy(worker.finished_saving)
    worker.save_matlab_fig(payload, "unused.fig", "Scan 1 - 2 nm", "Wavelength (nm)", "Transfer function (dB)", widget)
    assert spy.count() == 1
    return spy.at(0)


def _payload(traces):
    return json.dumps({"wavelengths_nm": [1510.0, 1520.0], "traces": traces})


def _trace(detector_id, values, color="#1f78b4"):
    return {"detector_id": detector_id, "label": f"Det {detector_id}", "values": values, "color": color}


def test_worker_renders_all_acquired_traces_with_identity_and_closes_only_owned_figure(qapp, monkeypatch):
    engine = _FakeMatlabEngine()
    widget, worker = _setup_worker(monkeypatch, engine)
    payload = _payload([_trace(3, [-30.0, -31.0], "#33a02c"), _trace(1, [-10.0, -11.0])])

    result = _call(worker, widget, payload)

    assert result == ["fig", True, "unused.fig"]
    plots = [call for call in engine.calls if call[0] == "plot"]
    assert [_matlab_values(call[1][0]) for call in plots] == [[1510.0, 1520.0], [1510.0, 1520.0]]
    assert [_matlab_values(call[1][1]) for call in plots] == [[-30.0, -31.0], [-10.0, -11.0]]
    assert [call[1][5] for call in plots] == ["Det 3", "Det 1"]
    rgb_mat = plots[0][1][3]
    assert not isinstance(rgb_mat, list)
    assert _matlab_values(rgb_mat) == [51 / 255, 160 / 255, 44 / 255]
    assert any(call[0] == "legend" for call in engine.calls)
    savefig_calls = [call for call in engine.calls if call[0] == "savefig"]
    assert len(savefig_calls) == 1
    assert savefig_calls[0][1] == ("worker-figure", "unused.fig")
    assert [call[1] for call in engine.calls if call[0] == "ylabel"] == [("Transfer function (dB)",)]
    assert [call[1] for call in engine.calls if call[0] == "close"] == [("worker-figure",)]
    assert not any(call[0] == "close" and call[1] == ("all",) for call in engine.calls)


def test_worker_keeps_single_detector_fig_supported(qapp, monkeypatch):
    engine = _FakeMatlabEngine()
    widget, worker = _setup_worker(monkeypatch, engine)

    result = _call(worker, widget, _payload([_trace(2, [-5.0, -6.0], "#e31a1c")]))

    assert result[1] is True
    plots = [call for call in engine.calls if call[0] == "plot"]
    assert len(plots) == 1
    assert _matlab_values(plots[0][1][0]) == [1510.0, 1520.0]
    assert _matlab_values(plots[0][1][1]) == [-5.0, -6.0]
    assert plots[0][1][5] == "Det 2"
    assert not any(call[0] == "quit" for call in engine.calls)


def test_worker_quits_only_a_locally_started_matlab_engine(qapp, monkeypatch):
    local_engine = _FakeMatlabEngine()
    widget, worker = _setup_worker(monkeypatch, local_engine)
    widget.get_matlab_engine = lambda: None
    plot_widgets.matlab.engine.start_matlab = lambda: local_engine

    result = _call(worker, widget, _payload([_trace(1, [1.0, 2.0])]))

    assert result[1] is True
    assert [call[0] for call in local_engine.calls].count("quit") == 1


def test_worker_passes_nan_and_infinities_through_to_matlab(qapp, monkeypatch):
    engine = _FakeMatlabEngine()
    widget, worker = _setup_worker(monkeypatch, engine)
    payload = _payload([_trace(1, [float("nan"), float("inf")])])

    result = _call(worker, widget, payload)

    values = _matlab_values([call[1][1] for call in engine.calls if call[0] == "plot"][0])
    assert result[1] is True
    assert values[0] != values[0]
    assert values[1] == float("inf")


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        json.dumps({"wavelengths_nm": [], "traces": [_trace(1, [])]}),
        json.dumps({"wavelengths_nm": [1.0], "traces": []}),
        _payload([_trace(1, [1.0, 2.0]), _trace(1, [3.0, 4.0])]),
        _payload([_trace(5, [1.0, 2.0])]),
        _payload([_trace(1, [1.0])]),
        _payload([_trace(1, [1.0, 2.0], "not-a-color")]),
    ],
)
def test_worker_rejects_invalid_payload_without_matlab_calls(qapp, monkeypatch, payload):
    engine = _FakeMatlabEngine()
    widget, worker = _setup_worker(monkeypatch, engine)

    result = _call(worker, widget, payload)

    assert result[0:2] == ["fig", False]
    assert "validating JSON data" in result[2]
    assert engine.calls == []


def test_worker_closes_owned_figure_after_matlab_operation_failure(qapp, monkeypatch):
    engine = _FakeMatlabEngine(fail_on="savefig")
    widget, worker = _setup_worker(monkeypatch, engine)

    result = _call(worker, widget, _payload([_trace(1, [1.0, 2.0])]))

    assert result[0:2] == ["fig", False]
    assert "savefig failed" in result[2]
    assert [call[1] for call in engine.calls if call[0] == "close"] == [("worker-figure",)]
    assert not any(call[0] == "quit" for call in engine.calls)
