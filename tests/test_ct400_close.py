from hardware.ct400 import CT400


class FakeDLL:
    def __init__(self, close_result=0):
        self.close_result = close_result
        self.close_calls = []
        self.laser_calls = []

    def CT400_Close(self, handle):
        self.close_calls.append(handle)
        return self.close_result

    def CT400_CmdLaser(self, *args):
        self.laser_calls.append(args)
        return 0


def _ct400_with_fake_dll(close_result=0):
    device = CT400.__new__(CT400)
    device.handle = 0x1234
    device.dll = FakeDLL(close_result)
    return device


def test_ct400_close_releases_live_handle_without_laser_command():
    device = _ct400_with_fake_dll()

    device.close()
    device.close()

    assert device.dll.close_calls == [0x1234]
    assert device.dll.laser_calls == []
    assert device.handle is None


def test_ct400_close_failure_is_logged_and_handle_is_not_reused(caplog):
    device = _ct400_with_fake_dll(close_result=-1)

    with caplog.at_level("WARNING", logger="LabApp.CT400"):
        device.close()

    assert device.dll.close_calls == [0x1234]
    assert device.dll.laser_calls == []
    assert device.handle is None
    assert "CT400_Close failed with return code -1" in caplog.text


def test_context_manager_exit_only_releases_native_resources():
    device = _ct400_with_fake_dll()

    device.__exit__(None, None, None)

    assert device.dll.close_calls == [0x1234]
    assert device.dll.laser_calls == []
    assert device.handle is None
