import inspect
from ctypes import c_int32, c_uint64

import pytest

from hardware.ct400 import CT400, CT400CommunicationError
from hardware.ct400_types import Enable, LaserInput, LaserSource
from hardware.dummy_ct400 import DummyCT400
from hardware.interfaces import AbstractCT400


@pytest.mark.parametrize(
    "method_name", ["set_laser", "cmd_laser", "set_detector_array", "set_external_synchronization"]
)
def test_concrete_laser_methods_match_abstract_signatures(method_name):
    expected = inspect.signature(getattr(AbstractCT400, method_name))
    expected_parameters = tuple(expected.parameters.values())[1:]

    for concrete in (CT400, DummyCT400):
        actual = inspect.signature(getattr(concrete, method_name))
        assert tuple(actual.parameters.values())[1:] == expected_parameters
        assert actual.return_annotation == expected.return_annotation


def test_dummy_laser_and_detector_methods_follow_the_shared_contract():
    device = DummyCT400()

    device.set_laser(LaserInput.LI_1, Enable.ENABLE, 1, LaserSource.LS_TunicsT100s_HP, 1500.0, 1600.0, 1)
    assert device.is_connected()

    device.cmd_laser(LaserInput.LI_1, Enable.ENABLE, 1550.0, 1.0)
    assert device._laser_enabled
    device.cmd_laser(
        laser_input=LaserInput.LI_1,
        enable=Enable.DISABLE,
        wavelength=1550.0,
        power=1.0,
    )
    assert not device.is_connected()
    assert not device._laser_enabled
    assert device.cmd_laser_calls == [
        ((), {"laser_input": LaserInput.LI_1, "enable": Enable.ENABLE, "wavelength": 1550.0, "power": 1.0}),
        ((), {"laser_input": LaserInput.LI_1, "enable": Enable.DISABLE, "wavelength": 1550.0, "power": 1.0}),
    ]

    device.set_detector_array(Enable.ENABLE, Enable.DISABLE, Enable.ENABLE, Enable.DISABLE)


def test_ct400_sync_file_rejects_an_uninitialized_handle():
    device = CT400.__new__(CT400)
    device.handle = None

    with pytest.raises(CT400CommunicationError, match="without an initialized CT400 handle"):
        device.save_scan_wavelength_sync_file("unused.txt")



class _FakeNativeFunction:
    def __init__(self, result=0):
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _FakeCT400DLL:
    def __init__(self, *, has_sync=True, sync_result=0):
        self.functions: dict[str, _FakeNativeFunction] = {}
        self.sync = _FakeNativeFunction(sync_result) if has_sync else None
        if self.sync is not None:
            self.functions["CT400_SetExternalSynchronization"] = self.sync

    def __getattr__(self, name: str):
        if name == "CT400_SetExternalSynchronization":
            raise AttributeError(name)
        if name.startswith("CT400_"):
            return self.functions.setdefault(name, _FakeNativeFunction())
        raise AttributeError(name)


def _mocked_ct400(*, has_sync=True, sync_result=0):
    device = CT400.__new__(CT400)
    device.handle = 0x1234
    device.dll = _FakeCT400DLL(has_sync=has_sync, sync_result=sync_result)
    device._configure_function_signatures()
    return device


def test_external_sync_declares_vendor_signature_and_only_changes_trigger_output():
    device = _mocked_ct400()
    sync = device.dll.sync
    assert sync is not None
    assert sync.restype is c_int32
    assert sync.argtypes == [c_uint64, c_int32]

    device.set_external_synchronization(Enable.ENABLE)
    device.set_external_synchronization(Enable.DISABLE)
    assert sync.calls == [(0x1234, 1), (0x1234, 0)]
    assert device.handle == 0x1234
    assert device.dll.functions["CT400_CmdLaser"].calls == []
    assert device.dll.functions["CT400_ScanStart"].calls == []


def test_optional_dll_export_absence_does_not_block_initialization_or_legacy_calls():
    device = _mocked_ct400(has_sync=False)
    assert device.dll.functions["CT400_SetScan"].argtypes is not None
    with pytest.raises(CT400CommunicationError, match="does not export"):
        device.set_external_synchronization(Enable.ENABLE)


@pytest.mark.parametrize("argument", [0, 1, False, True, "ENABLE", None])
def test_external_sync_rejects_non_enum_input_before_touching_vendor_dll(argument):
    device = _mocked_ct400()
    with pytest.raises(TypeError, match="Enable"):
        device.set_external_synchronization(argument)
    assert device.dll.sync.calls == []


@pytest.mark.parametrize("vendor_result", [-1, 1, 99])
def test_external_sync_checks_every_nonzero_native_result(vendor_result):
    device = _mocked_ct400(sync_result=vendor_result)
    with pytest.raises(CT400CommunicationError, match=f"raw return code: {vendor_result}"):
        device.set_external_synchronization(Enable.ENABLE)


def test_external_sync_requires_live_handle_without_native_call():
    device = _mocked_ct400()
    device.handle = None
    with pytest.raises(CT400CommunicationError, match="without an initialized CT400 handle"):
        device.set_external_synchronization(Enable.ENABLE)
    assert device.dll.sync.calls == []


def test_simulated_external_sync_does_not_command_laser_or_start_scan():
    device = DummyCT400()
    device.set_external_synchronization(Enable.ENABLE)
    device.set_external_synchronization(Enable.DISABLE)
    assert device.external_synchronization_calls == [Enable.ENABLE, Enable.DISABLE]
    assert not device._external_synchronization_enabled
    assert not device._is_scanning
    assert device.cmd_laser_calls == []
    with pytest.raises(TypeError, match="Enable"):
        device.set_external_synchronization(1)
