import inspect

import pytest

from hardware.ct400 import CT400, CT400CommunicationError
from hardware.ct400_types import Enable, LaserInput, LaserSource
from hardware.dummy_ct400 import DummyCT400
from hardware.interfaces import AbstractCT400


@pytest.mark.parametrize("method_name", ["set_laser", "cmd_laser", "set_detector_array"])
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
