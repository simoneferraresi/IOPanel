from abc import ABC, abstractmethod
from threading import Event

import numpy as np

# Import the new, clean types file, including structured errors.
from .ct400_types import (
    Detector,
    Enable,
    LaserInput,
    LaserSource,
    PowerData,
    ScanWaitResult,
)
from .pm5020_types import (
    BurstAcquisitionConfig,
    BurstPowerData,
    ChannelConfig,
    PM5020State,
    PowerChannel,
    PowerReading,
)


class AbstractCT400(ABC):
    """
    Defines the abstract interface for a CT400-like device.
    All public methods that the UI interacts with must be defined here.
    """

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def set_laser(
        self,
        laser_input: LaserInput,
        enable: Enable,
        gpib_address: int,
        laser_type: LaserSource,
        min_wavelength: float,
        max_wavelength: float,
        speed: int,
    ) -> None: ...

    @abstractmethod
    def cmd_laser(
        self,
        laser_input: LaserInput,
        enable: Enable,
        wavelength: float,
        power: float,
    ) -> None: ...

    @abstractmethod
    def set_sampling_res(self, resolution_pm: int) -> None: ...

    @abstractmethod
    def set_detector_array(self, det2: Enable, det3: Enable, det4: Enable, ext: Enable) -> None: ...

    @abstractmethod
    def set_scan(self, laser_power: float, min_wavelength: float, max_wavelength: float) -> None: ...

    @abstractmethod
    def start_scan(self) -> None: ...

    @abstractmethod
    def stop_scan(self) -> None: ...

    @abstractmethod
    def scan_wait_end(self) -> ScanWaitResult:
        """
        Blocks until the CT400 scan finishes and returns its documented result.

        This method now fully encapsulates the underlying C-style error buffer,
        making the interface safer and cleaner.

        Returns:
            The raw vendor return code and decoded tcError text. Unknown codes
            are preserved and classified as unexpected by ScanWaitResult.
        """

    @abstractmethod
    def get_data_points(self, dets_used: list[Detector]) -> tuple[np.ndarray, np.ndarray]: ...

    @abstractmethod
    def get_all_powers(self) -> PowerData: ...

    @abstractmethod
    def close(self) -> None: ...


class AbstractPM5020(ABC):
    """Small shared contract for the PM5020 hardware and simulated backends."""

    @abstractmethod
    def is_connected(self) -> bool: ...

    @property
    @abstractmethod
    def state(self) -> PM5020State: ...

    @property
    def is_ready(self) -> bool:
        """Whether ordinary power readings are currently permitted."""
        return self.state is PM5020State.IDLE

    @abstractmethod
    def connect(self, resource_name: str | None = None) -> None: ...

    @abstractmethod
    def configure_channel(self, channel: PowerChannel, config: ChannelConfig) -> None: ...

    @abstractmethod
    def read_power(self, channel: PowerChannel) -> PowerReading: ...

    @abstractmethod
    def configure_burst(self, config: BurstAcquisitionConfig) -> None: ...

    @abstractmethod
    def start_burst(self) -> None: ...

    @abstractmethod
    def retrieve_burst(
        self,
        *,
        max_samples: int | None = None,
        cancel_event: Event | None = None,
    ) -> BurstPowerData: ...

    @abstractmethod
    def close(self) -> None: ...
