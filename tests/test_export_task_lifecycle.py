"""Hardware-independent lifecycle regressions for both CSV/MAT export widgets."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject

from logic.export_task_lifecycle import ExportTaskLifecycle


@pytest.mark.parametrize("order", [("outcome", "finished"), ("finished", "outcome")])
@pytest.mark.parametrize("native_destroyed_first", [False, True])
def test_lifecycle_needs_result_finished_and_native_destruction(order, native_destroyed_first):
    """Thread lifetime, result callback and finished callback are distinct gates."""
    lifecycle = ExportTaskLifecycle[str](QObject())
    task = object()
    task_id = id(task)
    lifecycle.active_task = task
    lifecycle.owned_tasks[task_id] = task
    lifecycle._idle_notified = False

    if native_destroyed_first:
        lifecycle.release_destroyed(task_id)
        assert lifecycle.busy
        assert not lifecycle.take_idle_notification()

    for signal in order:
        if signal == "outcome":
            lifecycle.set_outcome("saved")
        else:
            lifecycle.set_thread_finished()
        if signal != order[-1]:
            assert lifecycle.take_ready_result() is None
            assert lifecycle.busy
            assert not lifecycle.take_idle_notification()

    assert lifecycle.take_ready_result() == "saved"
    assert lifecycle.take_ready_result() is None
    if not native_destroyed_first:
        assert lifecycle.busy
        assert not lifecycle.take_idle_notification()
        lifecycle.release_destroyed(task_id)

    assert not lifecycle.busy
    assert lifecycle.take_idle_notification()
    assert not lifecycle.take_idle_notification()


def test_lifecycle_rejects_second_export_while_native_thread_is_owned():
    """A finished worker awaiting QObject.destroyed is still not reusable."""
    lifecycle = ExportTaskLifecycle[str](QObject())
    task = object()
    lifecycle.owned_tasks[id(task)] = task

    with pytest.raises(RuntimeError, match="native export thread"):
        lifecycle.start(
            object(),
            on_outcome=lambda _value: None,
            on_thread_finished=lambda: None,
            on_destroyed=lambda _key: None,
        )


def test_lifecycle_only_releases_matching_task_id():
    lifecycle = ExportTaskLifecycle[str](QObject())
    active = object()
    owned = object()
    lifecycle.active_task = active
    lifecycle.owned_tasks[id(owned)] = owned
    lifecycle._idle_notified = False
    lifecycle.set_outcome("ok")
    lifecycle.set_thread_finished()
    assert lifecycle.take_ready_result() == "ok"
    lifecycle.release_destroyed(id(active))
    assert lifecycle.busy
    assert not lifecycle.take_idle_notification()
    lifecycle.release_destroyed(id(owned))
    assert lifecycle.take_idle_notification()
