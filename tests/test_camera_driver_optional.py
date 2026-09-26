from hardware import camera


def test_application_camera_modules_import_without_vimba_binding():
    import ui.discovery_dialog  # noqa: F401
    import ui.main_window  # noqa: F401


def test_camera_operations_fail_clearly_when_binding_is_unavailable(monkeypatch):
    monkeypatch.setattr(camera, "VIMBA_AVAILABLE", False)

    assert camera.VimbaCam.list_cameras() == []

    instance = camera.VimbaCam(identifier="test-camera", camera_name="Test Camera")
    messages = []
    instance.error.connect(messages.append)

    assert instance.open() is False
    assert messages
    assert "Vimba camera support is unavailable" in messages[0]
