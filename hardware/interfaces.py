from abc import ABC, abstractmethod
from ctypes import Array, c_char

import numpy as np

# Import from the new, clean types file. This breaks the circular dependency.
from .ct400_types import Detector, Enable, LaserInput, LaserSource, PowerData


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
    def scan_wait_end(self, error_buf: "Array[c_char]") -> int: ...

    @abstractmethod
    def get_data_points(self, dets_used: list[Detector]) -> tuple[np.ndarray, np.ndarray]: ...

    @abstractmethod
    def get_all_powers(self) -> PowerData: ...

    @abstractmethod
    def close(self) -> None: ...
