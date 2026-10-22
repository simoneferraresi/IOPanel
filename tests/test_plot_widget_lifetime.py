"""Registered native graphs stay owned until accepted, deferred teardown."""

import gc
from weakref import ref

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_recording import PowerMonitorAcquisitionSettings, PowerMonitorRecordingSample
from ui.plot_widgets import PowerMonitorTraceWidget


@pytest.mark.parametrize("registration", ["addWidget", "add_widget"])
@pytest.mark.parametrize("parented", [False, True])
@pytest.mark.parametrize("state", ["visible", "hidden", "closed"])
@pytest.mark.parametrize("delete_during_test", [False, True])
def test_registered_plot_survives_collection_until_native_teardown(
    qapp, qtbot, request, registration, parented, state, delete_during_test
):
    destroyed = []

    def create():
        owner = QWidget() if parented else PowerMonitorTraceWidget()
        trace = PowerMonitorTraceWidget(parent=owner) if parented else owner
        if parented:
            QVBoxLayout(owner).addWidget(trace)
        getattr(qtbot, registration)(owner)
        settings = PowerMonitorAcquisitionSettings(1550.0, "1.0", "mW", 1.0, LaserInput.LI_1, (Detector.DE_1,), 250)
        trace.start_recording(settings)
        trace.append_sample(PowerMonitorRecordingSample(0.25, 1.0, settings.detectors, (2.0,)))
        owner.show()
        graph = (owner, trace.plot_widget, trace.plot_widget.scene(), trace.plot_widget.getViewBox().childGroup)
        for index, obj in enumerate(graph):
            obj.destroyed.connect(lambda _obj=None, index=index: destroyed.append(index))
        if state == "hidden":
            owner.hide()
        elif state == "closed":
            assert owner.close()
        return ref(owner), ref(trace), tuple(ref(obj) for obj in graph)

    owner_ref, trace_ref, graph = create()
    # No test-local strong reference owns the root. Qt's native event dispatch
    # and pytest-qt's weak registrations cannot supply that ownership either.
    gc.collect()
    assert all(reference() is not None and shiboken6.isValid(reference()) for reference in graph)
    assert destroyed == []

    def verify_teardown():
        assert all(reference() is None or not shiboken6.isValid(reference()) for reference in graph)
        assert sorted(destroyed) == list(range(len(graph)))

    request.addfinalizer(verify_teardown)
    owner, trace = owner_ref(), trace_ref()
    owner.show()
    qtbot.waitExposed(owner)
    # Exercise a real repaint, autorange and hide/show after collection.
    paint_collections = []

    def collect_during_paint():
        gc.collect()
        assert all(reference() is not None and shiboken6.isValid(reference()) for reference in graph)
        paint_collections.append(True)

    trace.plot_widget.scene().sigPrepareForPaint.connect(collect_during_paint)
    trace.plot_widget.getViewBox().enableAutoRange()
    assert not trace.plot_widget.grab().isNull()
    assert paint_collections
    owner.hide()
    owner.show()
    qapp.processEvents()
    assert all(shiboken6.isValid(reference()) for reference in graph)
    assert owner.close()
    if delete_during_test:
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        verify_teardown()


def test_registered_plot_remains_owned_after_helper_raises(qtbot, request):
    graph = []
    closed = []

    def failing_helper():
        widget = PowerMonitorTraceWidget()
        qtbot.addWidget(widget, before_close_func=lambda target: closed.append(shiboken6.isValid(target)))
        graph.extend(ref(obj) for obj in (widget, widget.plot_widget, widget.plot_widget.scene()))
        widget.show()
        raise ValueError("failure after registration")

    with pytest.raises(ValueError, match="failure after registration"):
        failing_helper()
    gc.collect()
    assert all(reference() is not None and shiboken6.isValid(reference()) for reference in graph)

    def verify_teardown():
        assert closed == [True]
        assert all(reference() is None or not shiboken6.isValid(reference()) for reference in graph)

    request.addfinalizer(verify_teardown)
