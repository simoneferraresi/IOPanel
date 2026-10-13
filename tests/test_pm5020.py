from __future__ import annotations

from ctypes import c_bool, c_double, c_uint16
from threading import Event

import pytest

from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020 import (
    TLPMXPM5020,
    PM5020AcquisitionError,
    PM5020Cancelled,
    PM5020CommunicationError,
    PM5020DependencyError,
    PM5020StateError,
)
from hardware.pm5020_types import BurstAcquisitionConfig, BurstPowerData, ChannelConfig, PowerChannel


class FakeTLPMX:
    def __init__(self, *, sample_count: int = 205, fail_open: bool = False):
        self.sample_count = sample_count
        self.fail_open = fail_open
        self.calls: list[tuple] = []
        self.close_calls = 0
        self.fail_operation: str | None = None

    def _record(self, name: str, *args):
        self.calls.append((name, *args))
        if self.fail_operation == name:
            raise RuntimeError(f"{name} failed")

    def open(self, resource, query, reset):
        self._record("open", resource, query, reset)
        if self.fail_open:
            raise RuntimeError("device unavailable")

    def close(self):
        self.close_calls += 1
        self._record("close")

    def setFreqMode(self, *args):
        self._record("setFreqMode", *args)

    def setPowerAutoRange(self, *args):
        self._record("setPowerAutoRange", *args)

    def setPowerRange(self, *args):
        self._record("setPowerRange", *args)

    def setWavelength(self, *args):
        self._record("setWavelength", *args)

    def setInputFilterState(self, *args):
        self._record("setInputFilterState", *args)

    def measPower(self, output, channel):
        self._record("measPower", channel)
        output._obj.value = 0.02 if channel.value == 1 else 8e-7

    def confBurstArrayMeasPowerChannel(self, *args):
        self._record("confBurstArrayMeasPowerChannel", *args)

    def confBurstArrayMeasTrigger(self, *args):
        self._record("confBurstArrayMeasTrigger", *args)

    def startBurstArrayMeasurement(self):
        self._record("startBurstArrayMeasurement")

    def getBurstArraySamplesCount(self, output):
        self._record("getBurstArraySamplesCount")
        output._obj.value = self.sample_count

    def getBurstArraySamples(self, start, count, timestamps, ch1, ch2):
        self._record("getBurstArraySamples", start.value, count.value, len(timestamps))
        for i in range(count.value):
            index = start.value + i
            timestamps[i] = 1000 + index
            ch1[i] = index / 1000
            ch2[i] = index / 1_000_000


def driver_for(fake: FakeTLPMX | None = None) -> tuple[TLPMXPM5020, FakeTLPMX]:
    instance = fake or FakeTLPMX()
    return TLPMXPM5020(driver_factory=lambda: instance), instance


def test_backend_import_is_vendor_driver_free_and_missing_sdk_is_reported(monkeypatch):
    import importlib

    calls = []

    def fail_import(name):
        calls.append(name)
        raise ImportError("not installed")

    monkeypatch.setattr(importlib, "import_module", fail_import)
    meter = TLPMXPM5020()
    assert not meter.is_connected()
    assert calls == []
    with pytest.raises(PM5020DependencyError, match="Thorlabs Optical Power Meter SDK"):
        meter.connect("USB0::0x1313::0x80BB::SERIAL::INSTR")
    assert calls == ["TLPMX"]


@pytest.mark.parametrize("resource", [None, "", "   ", "USB\0INSTR", "USB::µ"])
def test_physical_backend_rejects_invalid_resource_names_before_driver_creation(resource):
    created = []
    meter = TLPMXPM5020(driver_factory=lambda: created.append(True))
    with pytest.raises(ValueError):
        meter.connect(resource)
    assert created == []


def test_physical_connection_failure_closes_partial_session_and_can_reconnect():
    failed = FakeTLPMX(fail_open=True)
    recovered = FakeTLPMX()
    instances = [failed, recovered]
    meter = TLPMXPM5020(driver_factory=lambda: instances.pop(0))
    with pytest.raises(PM5020CommunicationError, match="device unavailable"):
        meter.connect("USB0::CUSTOM::INSTR")
    assert failed.close_calls == 1
    assert not meter.is_connected()

    meter.connect("USB0::CUSTOM::INSTR")
    assert meter.is_connected()
    assert meter.resource_name == "USB0::CUSTOM::INSTR"
    open_call = next(call for call in recovered.calls if call[0] == "open")
    assert open_call[1].value == b"USB0::CUSTOM::INSTR"
    meter.close()
    meter.close()
    assert not meter.is_connected()


def test_native_driver_load_failure_has_sdk_and_bitness_guidance():
    def missing_native_dll():
        raise OSError("TLPMX_64.dll not found")

    meter = TLPMXPM5020(driver_factory=missing_native_dll)
    with pytest.raises(PM5020DependencyError, match="matching 32-bit or 64-bit architecture"):
        meter.connect("USB0::TEST::INSTR")


def test_physical_channel_configuration_and_single_power_read_use_watts():
    meter, fake = driver_for()
    meter.connect("USB0::TEST::INSTR")
    meter.configure_channel(
        PowerChannel.CH2,
        ChannelConfig(wavelength_nm=1550, auto_range=False, power_range_w=800e-9, input_filter_enabled=False),
    )
    reading = meter.read_power(PowerChannel.CH2)
    assert reading.channel is PowerChannel.CH2
    assert reading.power_w == pytest.approx(8e-7)
    assert reading.acquired_at_utc.tzinfo is not None
    names = [call[0] for call in fake.calls]
    assert names[1:7] == [
        "setFreqMode",
        "setPowerAutoRange",
        "setPowerRange",
        "setWavelength",
        "setInputFilterState",
        "measPower",
    ]
    assert fake.calls[1][1] == 0
    assert isinstance(fake.calls[1][2], c_uint16) and fake.calls[1][2].value == 2
    assert isinstance(fake.calls[2][1], c_bool) and fake.calls[2][1].value is False
    assert isinstance(fake.calls[2][2], c_uint16) and fake.calls[2][2].value == 2
    assert isinstance(fake.calls[3][1], c_double) and fake.calls[3][1].value == pytest.approx(800e-9)
    assert isinstance(fake.calls[3][2], c_uint16) and fake.calls[3][2].value == 2
    meter.close()


def test_physical_burst_config_and_chunked_retrieval_include_both_channels():
    meter, fake = driver_for()
    meter.connect("USB0::TEST::INSTR")
    config = BurstAcquisitionConfig(chunk_size=100)
    meter.configure_burst(config)
    meter.start_burst()
    with pytest.raises(PM5020StateError, match="busy or requires recovery"):
        meter.read_power(PowerChannel.CH1)
    result = meter.retrieve_burst()

    assert result.sample_count == 205
    assert result.timestamps_ticks[:3] == (1000, 1001, 1002)
    assert result.timestamps_ticks[-1] == 1204
    assert result.power_ch1_w[120] == pytest.approx(0.12)
    assert result.power_ch2_w[204] == pytest.approx(204e-6)
    assert [call[1:] for call in fake.calls if call[0] == "getBurstArraySamples"] == [
        (0, 100, 100),
        (100, 100, 100),
        (200, 5, 5),
    ]
    trigger = next(call for call in fake.calls if call[0] == "confBurstArrayMeasTrigger")
    assert tuple(value.value for value in trigger[1:]) == (3, 0, 1, 1)
    meter.close()


@pytest.mark.parametrize("sample_count", [0, 15_001])
def test_physical_burst_rejects_zero_and_oversized_sample_counts(sample_count):
    meter, _ = driver_for(FakeTLPMX(sample_count=sample_count))
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    with pytest.raises(PM5020AcquisitionError):
        meter.retrieve_burst()
    with pytest.raises(PM5020StateError, match="recovery"):
        meter.read_power(PowerChannel.CH1)
    meter.close()


def test_physical_burst_cancellation_and_vendor_failures_require_close_then_allow_reconnect():
    fake = FakeTLPMX()
    meter, _ = driver_for(fake)
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    cancel = Event()
    cancel.set()
    with pytest.raises(PM5020Cancelled):
        meter.retrieve_burst(cancel_event=cancel)
    meter.close()
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    fake.fail_operation = "getBurstArraySamples"
    with pytest.raises(PM5020CommunicationError, match="getBurstArraySamples failed"):
        meter.retrieve_burst()
    meter.close()
    meter.connect("USB0::TEST::INSTR")
    assert meter.is_connected()
    meter.close()


def test_physical_channel_configuration_failure_propagates_and_close_is_safe():
    fake = FakeTLPMX()
    fake.fail_operation = "setFreqMode"
    meter, _ = driver_for(fake)
    meter.connect("USB0::TEST::INSTR")
    with pytest.raises(PM5020CommunicationError, match="setFreqMode failed"):
        meter.configure_channel(PowerChannel.CH1, ChannelConfig())
    meter.close()
    meter.close()
    assert fake.close_calls == 1


def test_simulator_connect_read_disconnect_and_repeat_close():
    meter = DummyPM5020()
    assert not meter.is_connected()
    meter.connect()
    assert meter.is_connected()
    ch1 = meter.read_power(PowerChannel.CH1)
    ch2 = meter.read_power(PowerChannel.CH2)
    assert ch1.power_w == pytest.approx(0.01)
    assert ch2.power_w == pytest.approx(8e-7)
    assert ch1.acquired_at_utc.tzinfo is not None
    meter.close()
    meter.close()
    assert not meter.is_connected()


def test_simulator_configuration_validation_and_missing_sensor_errors():
    with pytest.raises(ValueError, match="power_range_w is required"):
        ChannelConfig(auto_range=False)
    with pytest.raises(ValueError, match="positive finite"):
        ChannelConfig(wavelength_nm=0)
    meter = DummyPM5020(available_channels={PowerChannel.CH1})
    meter.connect()
    with pytest.raises(PM5020CommunicationError, match="No simulated sensor"):
        meter.read_power(PowerChannel.CH2)


def test_simulator_burst_is_repeatable_and_includes_partial_final_chunk():
    meter = DummyPM5020(burst_sample_count=205)
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig(chunk_size=100))
    meter.start_burst()
    first = meter.retrieve_burst()
    meter.configure_burst(BurstAcquisitionConfig(chunk_size=100))
    meter.start_burst()
    second = meter.retrieve_burst()
    assert first == second
    assert first.sample_count == 205
    assert first.timestamps_ticks == tuple(i * 1000 for i in range(205))
    assert first.power_ch1_w[0] == pytest.approx(0.01)
    assert first.power_ch2_w[0] == pytest.approx(8e-7)
    assert first.power_ch1_w[-1] != first.power_ch1_w[0]


@pytest.mark.parametrize("sample_count", [0, 15001])
def test_simulator_rejects_zero_and_oversized_bursts(sample_count):
    meter = DummyPM5020(burst_sample_count=sample_count)
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    with pytest.raises(PM5020AcquisitionError):
        meter.retrieve_burst()


def test_simulator_cancellation_and_failure_are_recoverable_by_reconnection():
    meter = DummyPM5020(fail_operations={"read_power"})
    meter.connect()
    with pytest.raises(PM5020CommunicationError, match="Simulated PM5020 read_power"):
        meter.read_power(PowerChannel.CH1)
    meter.close()
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    cancel = Event()
    cancel.set()
    with pytest.raises(PM5020Cancelled):
        meter.retrieve_burst(cancel_event=cancel)
    meter.close()
    meter.connect()
    assert meter.is_connected()


def test_data_models_validate_units_shapes_and_unsigned_timestamp_ranges():
    with pytest.raises(ValueError, match="counts must match"):
        BurstPowerData((0, 1), (1.0,), (2.0, 3.0))
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        BurstPowerData((-1,), (1.0,), (2.0,))
    data = BurstPowerData((0,), (1e-3,), (2e-9,))
    assert data.power_ch1_w == (1e-3,)
    assert data.power_ch2_w == (2e-9,)
    with pytest.raises(ValueError, match="chunk_size"):
        BurstAcquisitionConfig(max_samples=10, chunk_size=11)


def test_monitoring_and_burst_acquisition_cannot_use_the_same_device_concurrently():
    meter = DummyPM5020()
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    with pytest.raises(PM5020StateError, match="busy"):
        meter.read_power(PowerChannel.CH1)
    burst = meter.retrieve_burst()
    assert burst.sample_count == 257
    assert meter.read_power(PowerChannel.CH1).power_w == pytest.approx(0.01)
