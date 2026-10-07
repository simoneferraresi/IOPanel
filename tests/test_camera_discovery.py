"""Deterministic tests for bounded exact-ID camera discovery."""

from __future__ import annotations

import threading

import pytest

from hardware.camera_discovery import (
    CameraDiscoveryCancelled,
    CameraNotFoundError,
    poll_camera_list,
    wait_for_camera_by_id,
    wait_for_cameras_by_id,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, delay: float):
        self.sleeps.append(delay)
        self.now += delay


class FakeCamera:
    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.enter_calls = 0

    def get_id(self):
        return self.camera_id

    def __enter__(self):
        self.enter_calls += 1
        return self


class SequenceSystem:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.polls = 0

    def get_all_cameras(self):
        index = min(self.polls, len(self.snapshots) - 1)
        self.polls += 1
        return self.snapshots[index]


def with_clock(clock: FakeClock):
    return {"clock": clock.monotonic, "sleeper": clock.sleep}


def test_immediate_discovery_returns_exact_camera_without_sleep():
    expected = FakeCamera("DEV_000F315B9CE1")
    system = SequenceSystem([[expected]])
    clock = FakeClock()

    result = wait_for_camera_by_id(system, expected.camera_id, **with_clock(clock))

    assert result is expected
    assert system.polls == 1
    assert clock.sleeps == []


def test_delayed_discovery_returns_on_first_poll_where_exact_id_appears():
    expected = FakeCamera("DEV_000F315B9CE1")
    system = SequenceSystem([[], [], [expected]])
    clock = FakeClock()

    result = wait_for_camera_by_id(
        system,
        expected.camera_id,
        timeout_s=2,
        poll_interval_s=0.25,
        **with_clock(clock),
    )

    assert result is expected
    assert system.polls == 3
    assert clock.sleeps == [0.25, 0.25]


def test_simulator_ids_never_substitute_for_requested_physical_id():
    simulators = [FakeCamera(camera_id) for camera_id in ("DEV_Cam1", "DEV_Cam2", "DEV_Cam3")]
    expected = FakeCamera("DEV_000F315B9CE1")
    system = SequenceSystem([simulators, simulators, [*simulators, expected]])
    clock = FakeClock()

    result = wait_for_camera_by_id(
        system,
        expected.camera_id,
        timeout_s=2,
        **with_clock(clock),
    )

    assert result is expected
    assert system.polls == 3
    assert all(camera.enter_calls == 0 for camera in simulators)


def test_timeout_reports_exact_missing_id_and_visible_ids_without_opening_camera():
    simulators = [FakeCamera(camera_id) for camera_id in ("DEV_Cam1", "DEV_Cam2", "DEV_Cam3")]
    system = SequenceSystem([simulators])
    clock = FakeClock()

    with pytest.raises(CameraNotFoundError) as caught:
        wait_for_camera_by_id(
            system,
            "DEV_000F315B9CE1",
            timeout_s=0.5,
            poll_interval_s=0.25,
            **with_clock(clock),
        )

    assert "DEV_000F315B9CE1" in str(caught.value)
    assert "0.5s" in str(caught.value)
    assert "DEV_Cam1" in str(caught.value)
    assert system.polls == 3
    assert all(camera.enter_calls == 0 for camera in simulators)


def test_two_camera_discovery_uses_one_shared_deadline_and_exact_ids():
    top = FakeCamera("DEV_000F315B9CE1")
    side = FakeCamera("DEV_000F315BA8F9")
    simulator = FakeCamera("DEV_Cam1")
    system = SequenceSystem([[simulator], [simulator, top], [simulator, top, side]])
    clock = FakeClock()

    result = wait_for_cameras_by_id(
        system,
        [top.camera_id, side.camera_id],
        timeout_s=1,
        poll_interval_s=0.25,
        **with_clock(clock),
    )

    assert result == {top.camera_id: top, side.camera_id: side}
    assert system.polls == 3
    assert clock.now == 0.5
    assert simulator.enter_calls == top.enter_calls == side.enter_calls == 0


def test_two_camera_timeout_names_only_missing_camera_at_shared_deadline():
    top = FakeCamera("DEV_000F315B9CE1")
    simulator = FakeCamera("DEV_Cam1")
    system = SequenceSystem([[simulator], [simulator, top]])
    clock = FakeClock()

    with pytest.raises(CameraNotFoundError) as caught:
        wait_for_cameras_by_id(
            system,
            [top.camera_id, "DEV_000F315BA8F9"],
            timeout_s=0.5,
            poll_interval_s=0.25,
            **with_clock(clock),
        )

    assert caught.value.missing_ids == ("DEV_000F315BA8F9",)
    assert clock.now == 0.5
    assert system.polls == 3
    assert top.enter_calls == simulator.enter_calls == 0


def test_cancelled_discovery_returns_promptly_without_polling():
    event = threading.Event()
    event.set()
    system = SequenceSystem([[]])

    with pytest.raises(CameraDiscoveryCancelled):
        wait_for_camera_by_id(system, "DEV_000F315B9CE1", cancel_event=event)

    assert system.polls == 0


def test_cancellation_during_poll_exits_before_next_discovery_query():
    event = threading.Event()
    system = SequenceSystem([[], []])
    timer = threading.Timer(0.01, event.set)
    timer.start()

    with pytest.raises(CameraDiscoveryCancelled):
        wait_for_camera_by_id(
            system,
            "DEV_000F315B9CE1",
            timeout_s=10,
            poll_interval_s=1,
            cancel_event=event,
        )
    timer.join()

    assert system.polls == 1


def test_manual_list_poll_publishes_devices_as_they_arrive():
    simulator = FakeCamera("DEV_Cam1")
    physical = FakeCamera("DEV_000F315B9CE1")
    system = SequenceSystem([[], [simulator], [simulator, physical]])
    clock = FakeClock()
    updates = []

    result = poll_camera_list(
        system,
        timeout_s=0.5,
        poll_interval_s=0.25,
        on_update=lambda cameras: updates.append([camera.get_id() for camera in cameras]),
        **with_clock(clock),
    )

    assert updates == [[], ["DEV_Cam1"], ["DEV_Cam1", "DEV_000F315B9CE1"]]
    assert [camera.get_id() for camera in result] == ["DEV_Cam1", "DEV_000F315B9CE1"]
