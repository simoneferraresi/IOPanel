"""Hardware-independent tests for VmbPy camera context ownership."""

from __future__ import annotations

import pytest

from hardware import camera


class FakeCamera:
    def __init__(self, *, enter_error=None, start_error=None):
        self.enter_error = enter_error
        self.start_error = start_error
        self.enter_calls = 0
        self.exit_calls = 0
        self.start_calls = 0

    def get_id(self):
        return "fake-camera"

    def __enter__(self):
        self.enter_calls += 1
        if self.enter_error:
            raise self.enter_error
        return self

    def __exit__(self, *_args):
        self.exit_calls += 1

    def start_streaming(self, *_args, **_kwargs):
        self.start_calls += 1
        if self.start_error:
            raise self.start_error

    def stop_streaming(self):
        pass


def make_camera(monkeypatch, device):
    class System:
        class Instance:
            def get_all_cameras(self):
                return [device]

        @staticmethod
        def get_instance():
            return System.Instance()

    monkeypatch.setattr(camera, "VIMBA_AVAILABLE", True)
    monkeypatch.setattr(camera, "VmbSystem", System)
    instance = camera.VimbaCam("fake-camera")
    return instance


@pytest.mark.parametrize("failure_point", ["configure", "cache"])
def test_partial_initialization_failure_releases_entered_context(monkeypatch, failure_point):
    device = FakeCamera()
    instance = make_camera(monkeypatch, device)
    errors = []
    connected = []
    instance.error.connect(errors.append)
    instance.connected.connect(lambda: connected.append(True))
    failure = camera.VmbCameraError(f"{failure_point} failed")

    if failure_point == "configure":
        monkeypatch.setattr(instance, "_configure_camera", lambda: (_ for _ in ()).throw(failure))
    else:
        monkeypatch.setattr(instance, "_configure_camera", lambda: None)
        monkeypatch.setattr(instance, "_update_settings_cache", lambda: (_ for _ in ()).throw(failure))

    assert instance.open() is False
    assert device.enter_calls == 1
    assert device.exit_calls == 1
    assert device.start_calls == 0
    assert instance.device is None
    assert instance.is_streaming is False
    assert connected == []
    assert errors


def test_successful_internal_open_transfers_context_to_device_until_close(monkeypatch):
    device = FakeCamera()
    instance = make_camera(monkeypatch, device)
    monkeypatch.setattr(instance, "_configure_camera", lambda: None)
    monkeypatch.setattr(instance, "_update_settings_cache", lambda: None)

    assert instance._open_device_internal() is True
    assert instance.device is device
    assert device.enter_calls == 1
    assert device.exit_calls == 0

    instance.close()
    assert device.exit_calls == 1
    assert instance.device is None


def test_recovery_uses_bounded_discovery_after_camera_temporarily_disappears(monkeypatch):
    device = FakeCamera()
    system = type("System", (), {"get_all_cameras": lambda _self: snapshots.pop(0) if snapshots else [device]})()
    snapshots = [[], [device]]

    class VmbSystem:
        @staticmethod
        def get_instance():
            return system

    monkeypatch.setattr(camera, "VIMBA_AVAILABLE", True)
    monkeypatch.setattr(camera, "VmbSystem", VmbSystem)
    instance = camera.VimbaCam("fake-camera")
    monkeypatch.setattr(instance, "_configure_camera", lambda: None)
    monkeypatch.setattr(instance, "_update_settings_cache", lambda: None)
    monkeypatch.setattr(camera.time, "sleep", lambda _delay: None)

    assert instance.recover_once() is True
    assert device.enter_calls == 1
    assert device.start_calls == 1
    assert instance.device is device
    instance.close()


def test_streaming_start_failure_closes_transferred_context_once(monkeypatch):
    failure = camera.VmbCameraError("stream start failed")
    device = FakeCamera(start_error=failure)
    instance = make_camera(monkeypatch, device)
    monkeypatch.setattr(instance, "_configure_camera", lambda: None)
    monkeypatch.setattr(instance, "_update_settings_cache", lambda: None)
    connected = []
    instance.connected.connect(lambda: connected.append(True))

    assert instance.open() is False
    assert device.enter_calls == 1
    assert device.start_calls == 1
    assert device.exit_calls == 1
    assert instance.device is None
    assert instance.is_streaming is False
    assert connected == []


def test_context_entry_failure_does_not_exit_unentered_context(monkeypatch):
    device = FakeCamera(enter_error=camera.VmbCameraError("entry failed"))
    instance = make_camera(monkeypatch, device)

    assert instance.open() is False
    assert device.enter_calls == 1
    assert device.exit_calls == 0
    assert instance.device is None
    assert instance.is_streaming is False


def test_camera_open_reports_discovery_timeout_as_missing_camera(monkeypatch):
    class System:
        @staticmethod
        def get_instance():
            return object()

    monkeypatch.setattr(camera, "VIMBA_AVAILABLE", True)
    monkeypatch.setattr(camera, "VmbSystem", System)
    monkeypatch.setattr(
        camera,
        "wait_for_camera_by_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            camera.CameraNotFoundError(("fake-camera",), 10, ("DEV_Cam1",))
        ),
    )
    instance = camera.VimbaCam("fake-camera")
    errors = []
    instance.error.connect(errors.append)

    assert instance.open() is False
    assert any("Camera discovery timeout" in message for message in errors)
    assert instance.device is None


def test_unexpected_post_entry_failure_keeps_outer_open_cleanup_path(monkeypatch):
    device = FakeCamera()
    instance = make_camera(monkeypatch, device)
    monkeypatch.setattr(
        instance,
        "_configure_camera",
        lambda: (_ for _ in ()).throw(RuntimeError("unexpected configuration defect")),
    )
    errors = []
    instance.error.connect(errors.append)

    assert instance.open() is False
    assert device.enter_calls == 1
    assert device.exit_calls == 1
    assert instance.device is None
    assert instance.is_streaming is False
    assert any("Unexpected open error: unexpected configuration defect" in message for message in errors)


@pytest.mark.parametrize("failure_point", ["configure", "cache"])
def test_close_after_partial_open_rollback_does_not_exit_twice(monkeypatch, failure_point):
    device = FakeCamera()
    instance = make_camera(monkeypatch, device)
    failure = camera.VmbCameraError(f"{failure_point} failed")
    if failure_point == "configure":
        monkeypatch.setattr(instance, "_configure_camera", lambda: (_ for _ in ()).throw(failure))
    else:
        monkeypatch.setattr(instance, "_configure_camera", lambda: None)
        monkeypatch.setattr(instance, "_update_settings_cache", lambda: (_ for _ in ()).throw(failure))

    assert instance.open() is False
    instance.close()  # CameraInitWorker follows this pattern after open() fails.
    assert device.exit_calls == 1
    assert instance.device is None


def test_rollback_exit_failure_is_logged_without_hiding_initialization_error(monkeypatch, caplog):
    class ExitFailureCamera(FakeCamera):
        def __exit__(self, *_args):
            self.exit_calls += 1
            raise RuntimeError("cleanup failed")

    device = ExitFailureCamera()
    instance = make_camera(monkeypatch, device)
    failure = camera.VmbCameraError("configuration failed")
    monkeypatch.setattr(instance, "_configure_camera", lambda: (_ for _ in ()).throw(failure))

    assert instance.open() is False
    assert device.exit_calls == 1
    assert instance.device is None
    assert "configuration failed" in caplog.text
    assert "cleanup failed" in caplog.text
