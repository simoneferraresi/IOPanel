from __future__ import annotations

from ctypes import c_bool, c_double, c_uint16
from threading import Event, Thread

import pytest

from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020 import (
    TLPMXPM5020,
    PM5020AcquisitionError,
    PM5020Cancelled,
    PM5020CommunicationError,
    PM5020DependencyError,
    PM5020Error,
    PM5020StateError,
)
from hardware.pm5020_types import BurstAcquisitionConfig, BurstPowerData, ChannelConfig, PM5020State, PowerChannel


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


class BlockingFakeTLPMX(FakeTLPMX):
    def __init__(self):
        super().__init__(sample_count=3)
        self.retrieval_entered = Event()
        self.release_retrieval = Event()

    def getBurstArraySamples(self, start, count, timestamps, ch1, ch2):
        self.retrieval_entered.set()
        assert self.release_retrieval.wait(timeout=3)
        super().getBurstArraySamples(start, count, timestamps, ch1, ch2)


def driver_for(fake: FakeTLPMX | None = None) -> tuple[TLPMXPM5020, FakeTLPMX]:
    instance = fake or FakeTLPMX()
    return TLPMXPM5020(driver_factory=lambda: instance), instance


def test_backend_import_is_vendor_driver_free_and_missing_sdk_is_reported(monkeypatch):
    from hardware import pm5020

    calls = []

    def missing_wrapper(name):
        calls.append(name)
        raise ImportError("TLPMX is unavailable")

    monkeypatch.setattr(pm5020.importlib, "import_module", missing_wrapper)
    meter = TLPMXPM5020()
    assert not meter.is_connected()
    assert calls == []
    with pytest.raises(PM5020DependencyError, match="not importable"):
        meter.connect("USB0::0x1313::0x80BB::SERIAL::INSTR")
    assert calls == ["TLPMX"]


def test_legacy_importable_sdk_fallback_when_both_paths_absent(monkeypatch):
    from hardware import pm5020

    fake = FakeTLPMX()
    requested = []

    def vendor_import(name):
        requested.append(name)
        return type("ImportableSDK", (), {"TLPMX": lambda: fake})()

    monkeypatch.setattr(pm5020.importlib, "import_module", vendor_import)
    meter = TLPMXPM5020()
    assert requested == []
    meter.connect("USB0::TEST::INSTR")
    assert requested == ["TLPMX"]
    assert fake.calls[0][0] == "open"
    meter.close()
    assert fake.close_calls == 1


@pytest.mark.parametrize("missing_path", ["wrapper", "dll"])
def test_partial_sdk_paths_are_rejected_without_import(missing_path, monkeypatch):
    from hardware import pm5020

    monkeypatch.setattr(
        pm5020.importlib,
        "import_module",
        lambda name: pytest.fail("Partially configured SDK must not fall back to global import"),
    )
    kwargs = {"dll_path": "vendor.dll"} if missing_path == "wrapper" else {"wrapper_path": "vendor.py"}
    meter = TLPMXPM5020(**kwargs)
    with pytest.raises(PM5020DependencyError, match="requires both"):
        meter.connect("USB0::TEST::INSTR")


def test_dll_search_handles_outlive_native_driver_close(monkeypatch):
    from hardware import pm5020

    events = []
    fake = FakeTLPMX()
    actual_close = fake.close

    def driver_close():
        events.append("driver.close")
        actual_close()

    fake.close = driver_close

    class Handle:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(self.name)

    monkeypatch.setattr(
        pm5020,
        "_load_thorlabs_factory",
        lambda wrapper, dll: (lambda: fake, (Handle("stage"), Handle("vendor"))),
    )
    meter = TLPMXPM5020(wrapper_path="vendor.py", dll_path="native.dll")
    meter.connect("USB0::TEST::INSTR")
    assert events == []
    meter.close()
    assert events == ["driver.close", "vendor", "stage"]
    meter.close()
    assert events == ["driver.close", "vendor", "stage"]


@pytest.mark.parametrize("fail_during", ["open", "close"])
def test_dll_search_handles_release_after_failed_native_cleanup(monkeypatch, fail_during):
    from hardware import pm5020

    events = []
    fake = FakeTLPMX(fail_open=fail_during == "open")
    actual_close = fake.close

    def driver_close():
        events.append("driver.close")
        actual_close()
        if fail_during == "close":
            raise RuntimeError("native close failed")

    fake.close = driver_close

    class Handle:
        def close(self):
            events.append("handle.close")

    monkeypatch.setattr(
        pm5020,
        "_load_thorlabs_factory",
        lambda wrapper, dll: (lambda: fake, (Handle(),)),
    )
    meter = TLPMXPM5020(wrapper_path="vendor.py", dll_path="native.dll")
    if fail_during == "open":
        with pytest.raises(PM5020CommunicationError, match="Could not open"):
            meter.connect("USB0::TEST::INSTR")
    else:
        meter.connect("USB0::TEST::INSTR")
        with pytest.raises(PM5020CommunicationError, match="Failed to close"):
            meter.close()
    assert events == ["driver.close", "handle.close"]
    assert not meter.is_connected()


def test_configured_sdk_files_are_staged_together_and_cache_invalidates(tmp_path, monkeypatch):
    import sys

    from hardware.pm5020 import _load_thorlabs_factory

    wrapper = tmp_path / "vendor" / "TLPMX.py"
    dll = tmp_path / "bin" / "TLPMX_64.dll"
    wrapper.parent.mkdir()
    dll.parent.mkdir()
    wrapper.write_text("class TLPMX:\n    pass\n", encoding="utf-8")
    dll.write_bytes(b"native-sdk-v1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))

    factory, _handle = _load_thorlabs_factory(wrapper, dll)
    module = sys.modules[factory.__module__]
    staged_dir = __import__("pathlib").Path(module.__file__).parent
    assert (staged_dir / "TLPMX.py").read_bytes() == wrapper.read_bytes()
    assert (staged_dir / "TLPMX_64.dll").read_bytes() == dll.read_bytes()

    dll.write_bytes(b"native-sdk-v2")
    replacement_factory, _handle = _load_thorlabs_factory(wrapper, dll)
    replacement_module = sys.modules[replacement_factory.__module__]
    assert replacement_module.__file__ != module.__file__
    assert (
        __import__("pathlib").Path(replacement_module.__file__).parent / "TLPMX_64.dll"
    ).read_bytes() == dll.read_bytes()


@pytest.mark.parametrize("missing", ["wrapper", "dll"])
def test_configured_sdk_loader_reports_missing_source_file(tmp_path, missing):
    from hardware.pm5020 import _load_thorlabs_factory

    wrapper = tmp_path / "TLPMX.py"
    dll = tmp_path / "TLPMX_64.dll"
    wrapper.write_text("class TLPMX:\n    pass\n", encoding="utf-8")
    dll.write_bytes(b"test")
    if missing == "wrapper":
        wrapper.unlink()
    else:
        dll.unlink()
    with pytest.raises(PM5020DependencyError, match="was not found"):
        _load_thorlabs_factory(wrapper, dll)


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


def test_physical_state_transitions_and_unknown_channel_failure_require_reconnection():
    fake = FakeTLPMX()
    meter, _ = driver_for(fake)
    assert meter.state is PM5020State.DISCONNECTED
    assert not meter.is_ready
    meter.connect("USB0::TEST::INSTR")
    assert meter.state is PM5020State.IDLE
    assert meter.is_ready

    fake.fail_operation = "measPower"
    with pytest.raises(PM5020CommunicationError, match="Could not read PM5020 channel 2: measPower failed"):
        meter.read_power(PowerChannel.CH2)
    assert meter.state is PM5020State.ERROR
    assert meter.is_connected()
    assert not meter.is_ready
    with pytest.raises(PM5020StateError):
        meter.read_power(PowerChannel.CH1)

    meter.close()
    assert meter.state is PM5020State.DISCONNECTED
    fake.fail_operation = None
    meter.connect("USB0::TEST::INSTR")
    assert meter.state is PM5020State.IDLE
    assert meter.read_power(PowerChannel.CH1).power_w == pytest.approx(0.02)
    meter.close()


@pytest.mark.parametrize("capacity", [500, 1000])
def test_physical_burst_capacity_defaults_to_configuration_and_accepts_explicit_limits(capacity):
    meter, _ = driver_for(FakeTLPMX(sample_count=400))
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000, chunk_size=100))
    meter.start_burst()
    result = meter.retrieve_burst(max_samples=capacity)
    assert result.sample_count == 400
    assert meter.state is PM5020State.IDLE
    meter.close()


def test_physical_default_burst_capacity_uses_active_configuration():
    meter, _ = driver_for(FakeTLPMX(sample_count=999))
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000, chunk_size=100))
    meter.start_burst()
    assert meter.retrieve_burst().sample_count == 999
    assert meter.state is PM5020State.IDLE
    meter.close()


@pytest.mark.parametrize("capacity", [0, -1, 1001, 15_001, True, 1.5])
def test_physical_invalid_burst_capacity_is_rejected_before_driver_calls_and_stays_armed(capacity):
    meter, fake = driver_for(FakeTLPMX(sample_count=1))
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000, chunk_size=100))
    meter.start_burst()
    calls_before = len(fake.calls)
    with pytest.raises(ValueError):
        meter.retrieve_burst(max_samples=capacity)
    assert len(fake.calls) == calls_before
    assert meter.state is PM5020State.ARMED
    meter.close()


def test_physical_reported_count_over_effective_capacity_requires_recovery():
    meter, fake = driver_for(FakeTLPMX(sample_count=401))
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000))
    meter.start_burst()
    with pytest.raises(PM5020AcquisitionError, match="exceeding capacity 400"):
        meter.retrieve_burst(max_samples=400)
    assert meter.state is PM5020State.ERROR
    assert [call[0] for call in fake.calls].count("getBurstArraySamples") == 0
    meter.close()


def test_status_is_observable_during_blocked_retrieval_while_io_remains_serialized():
    fake = BlockingFakeTLPMX()
    meter, _ = driver_for(fake)
    meter.connect("USB0::TEST::INSTR")
    meter.configure_burst(BurstAcquisitionConfig(chunk_size=3))
    meter.start_burst()
    retrieval_result = []
    thread_errors = []

    def retrieve():
        try:
            retrieval_result.append(meter.retrieve_burst())
        except PM5020Error as exc:
            thread_errors.append(exc)

    retrieval = Thread(target=retrieve)
    retrieval.start()
    assert fake.retrieval_entered.wait(timeout=2)
    assert meter.state is PM5020State.RETRIEVING
    assert not meter.is_ready

    read_started = Event()
    read_finished = Event()

    def read_ch1():
        read_started.set()
        try:
            meter.read_power(PowerChannel.CH1)
            read_finished.set()
        except PM5020Error as exc:
            thread_errors.append(exc)

    reader = Thread(target=read_ch1)
    reader.start()
    assert read_started.wait(timeout=2)
    fake.release_retrieval.set()
    retrieval.join(timeout=2)
    reader.join(timeout=2)
    assert not retrieval.is_alive()
    assert not reader.is_alive()
    assert read_finished.is_set()
    assert not thread_errors
    assert retrieval_result[0].sample_count == 3
    assert meter.state is PM5020State.IDLE
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
    assert meter.state is PM5020State.ERROR
    assert meter.is_connected()
    assert not meter.is_ready
    meter.close()
    meter.close()
    assert fake.close_calls == 1
    assert meter.state is PM5020State.DISCONNECTED


def test_simulator_connect_read_disconnect_and_repeat_close():
    meter = DummyPM5020()
    assert not meter.is_connected()
    assert meter.state is PM5020State.DISCONNECTED
    meter.connect()
    assert meter.is_connected()
    assert meter.state is PM5020State.IDLE
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
    assert meter.state is PM5020State.ERROR
    with pytest.raises(PM5020StateError):
        meter.read_power(PowerChannel.CH1)


def test_simulator_empty_and_partial_configurations_are_preserved():
    default = DummyPM5020()
    default.connect()
    assert default.read_power(PowerChannel.CH1).power_w == pytest.approx(0.01)
    assert default.read_power(PowerChannel.CH2).power_w == pytest.approx(8e-7)

    no_sensors = DummyPM5020(available_channels=set())
    no_sensors.connect()
    with pytest.raises(PM5020CommunicationError, match="No simulated sensor"):
        no_sensors.read_power(PowerChannel.CH1)
    assert no_sensors.state is PM5020State.ERROR

    no_levels = DummyPM5020(power_levels_w={})
    no_levels.connect()
    with pytest.raises(PM5020CommunicationError, match="No simulated power level"):
        no_levels.read_power(PowerChannel.CH1)
    assert no_levels.state is PM5020State.ERROR

    ch1_only = DummyPM5020(available_channels={PowerChannel.CH1})
    ch1_only.connect()
    assert ch1_only.read_power(PowerChannel.CH1).power_w == pytest.approx(0.01)
    with pytest.raises(PM5020CommunicationError, match="No simulated sensor"):
        ch1_only.read_power(PowerChannel.CH2)

    ch2_only = DummyPM5020(
        available_channels={PowerChannel.CH2},
        power_levels_w={PowerChannel.CH2: 2e-6},
    )
    ch2_only.connect()
    assert ch2_only.read_power(PowerChannel.CH2).power_w == pytest.approx(2e-6)

    missing_level = DummyPM5020(power_levels_w={PowerChannel.CH1: 0.03})
    missing_level.connect()
    assert missing_level.read_power(PowerChannel.CH1).power_w == pytest.approx(0.03)
    with pytest.raises(PM5020CommunicationError, match="No simulated power level"):
        missing_level.read_power(PowerChannel.CH2)


@pytest.mark.parametrize("capacity", [500, 1000])
def test_simulator_burst_capacity_defaults_to_configuration_and_accepts_explicit_limits(capacity):
    meter = DummyPM5020(burst_sample_count=400)
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000, chunk_size=100))
    meter.start_burst()
    assert meter.retrieve_burst(max_samples=capacity).sample_count == 400
    assert meter.state is PM5020State.IDLE


def test_simulator_default_burst_capacity_uses_active_configuration():
    meter = DummyPM5020(burst_sample_count=999)
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000, chunk_size=100))
    meter.start_burst()
    assert meter.retrieve_burst().sample_count == 999


@pytest.mark.parametrize("capacity", [0, -1, 1001, 15_001, True, 1.5])
def test_simulator_invalid_capacity_is_rejected_before_retrieval_and_stays_armed(capacity):
    meter = DummyPM5020(burst_sample_count=1)
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000))
    meter.start_burst()
    with pytest.raises(ValueError):
        meter.retrieve_burst(max_samples=capacity)
    assert meter.state is PM5020State.ARMED


def test_simulator_reported_count_over_effective_capacity_requires_recovery():
    meter = DummyPM5020(burst_sample_count=401)
    meter.connect()
    meter.configure_burst(BurstAcquisitionConfig(max_samples=1000))
    meter.start_burst()
    with pytest.raises(PM5020AcquisitionError, match="exceeding capacity 400"):
        meter.retrieve_burst(max_samples=400)
    assert meter.state is PM5020State.ERROR


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
    assert meter.state is PM5020State.ERROR
    with pytest.raises(PM5020StateError):
        meter.read_power(PowerChannel.CH2)
    meter.close()
    assert meter.state is PM5020State.DISCONNECTED
    meter.connect()
    assert meter.state is PM5020State.IDLE
    meter.configure_burst(BurstAcquisitionConfig())
    meter.start_burst()
    cancel = Event()
    cancel.set()
    with pytest.raises(PM5020Cancelled):
        meter.retrieve_burst(cancel_event=cancel)
    assert meter.state is PM5020State.ERROR
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
