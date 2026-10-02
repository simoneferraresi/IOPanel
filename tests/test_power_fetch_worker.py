from PySide6.QtTest import QSignalSpy

from hardware.ct400_types import PowerData
from ui.control_panel import PowerFetchWorker


def test_missing_ct400_does_not_suppress_later_power_fetch():
    worker = PowerFetchWorker(None)
    errors = QSignalSpy(worker.error_occurred)
    data_ready = QSignalSpy(worker.data_ready)

    worker.fetch_power()

    assert errors.count() == 1
    assert errors.at(0)[0] == "CT400 device is not available in worker."

    expected_data = PowerData(pout=1.25, detectors={})

    class FakeCT400:
        def __init__(self):
            self.read_count = 0

        def get_all_powers(self) -> PowerData:
            self.read_count += 1
            return expected_data

    fake_ct400 = FakeCT400()
    worker.ct400 = fake_ct400

    worker.fetch_power()

    assert fake_ct400.read_count == 1
    assert data_ready.count() == 1
    assert data_ready.at(0)[0] == expected_data
