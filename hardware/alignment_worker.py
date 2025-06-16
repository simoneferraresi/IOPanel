# hardware/alignment_worker.py

import logging
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from hardware.ct400_types import Detector
from hardware.interfaces import AbstractCT400

# --- MODIFICATION: Do NOT import 'Axis' ---
from hardware.piezo import PiezoController

logger = logging.getLogger("LabApp.AlignmentWorker")


@dataclass
class AlignmentSettings:
    """Holds all parameters for an alignment task."""

    iterations: int
    step_nm: float
    samples_per_point: int
    coupling_type: str  # "butt" or "top"


@dataclass
class MappingSettings:
    """Holds all parameters for a 2D power mapping task."""

    x_min_nm: int
    x_max_nm: int
    x_step_nm: int
    y_min_nm: int
    y_max_nm: int
    y_step_nm: int
    samples_per_point: int
    stage_to_map: str  # "left" or "right"


class AlignmentWorker(QObject):
    """
    Worker to perform iterative optical alignment using piezo stages and a power meter.
    Runs in a separate thread to avoid blocking the GUI.
    """

    progress_updated = Signal(str, float)
    alignment_finished = Signal(str, float)
    error_occurred = Signal(str)
    mapping_progress = Signal(int, int)  # percentage, total_points
    mapping_finished = Signal(object, object, object)  # x_coords, y_coords, z_power_grid

    def __init__(
        self,
        ct400: AbstractCT400,
        piezo_left: PiezoController,
        piezo_right: PiezoController,
    ):
        super().__init__()
        self.ct400 = ct400
        self.piezo_left = piezo_left
        self.piezo_right = piezo_right
        self._is_running = False

    @Slot()
    def stop(self):
        logger.info("Alignment worker stop requested.")
        self._is_running = False

    @Slot(AlignmentSettings)
    def run_alignment(self, settings: AlignmentSettings):
        self._is_running = True
        logger.info(f"Starting alignment with settings: {settings}")
        try:
            for i in range(settings.iterations):
                if not self._is_running:
                    raise InterruptedError("Alignment cancelled by user.")

                self.progress_updated.emit(f"Iteration {i + 1}/{settings.iterations}: Aligning Left Stage...", -999)
                self._align_stage(self.piezo_left, settings)

                if not self._is_running:
                    raise InterruptedError("Alignment cancelled by user.")

                self.progress_updated.emit(f"Iteration {i + 1}/{settings.iterations}: Aligning Right Stage...", -999)
                self._align_stage(self.piezo_right, settings)

            final_power = self._read_power(settings.samples_per_point)
            self.alignment_finished.emit("Alignment successful", final_power)

        except InterruptedError as e:
            logger.warning(f"Alignment process interrupted: {e}")
            self.alignment_finished.emit("Alignment cancelled", -999)
        except Exception as e:
            logger.exception("An error occurred during alignment.")
            self.error_occurred.emit(f"Error during alignment: {e}")
        finally:
            self._is_running = False

    def _align_stage(self, piezo: PiezoController, settings: AlignmentSettings):
        """Performs the hill-climbing algorithm on a single piezo stage."""
        if settings.coupling_type == "butt" and piezo == self.piezo_left:
            # --- MODIFICATION: Use strings instead of Enum ---
            axes_to_align = ["z", "x"]
        else:
            # --- MODIFICATION: Use strings instead of Enum ---
            axes_to_align = ["y", "x"]

        for axis in axes_to_align:
            if not self._is_running:
                return
            # --- MODIFICATION: Use simple string formatting ---
            self.progress_updated.emit(f"Aligning {piezo.port} Axis-{axis}...", -999)
            self._climb_hill(piezo, axis, settings)

    def _climb_hill(self, piezo: PiezoController, axis: str, settings: AlignmentSettings):
        """The core single-axis alignment logic. (Corrected)"""
        initial_power = self._read_power(settings.samples_per_point)
        max_power = initial_power

        # Store the best position found so far
        max_power_pos_v = piezo.get_voltage(axis)

        logger.debug(f"Starting {axis}-axis climb. Initial power: {initial_power:.3f} dBm at {max_power_pos_v:.3f} V")
        self.progress_updated.emit(f"Starting {axis}-axis climb...", max_power)

        direction = 1
        has_flipped_direction = False

        while self._is_running:
            # Get voltage before moving
            last_pos_v = piezo.get_voltage(axis)

            # Move the stage
            piezo.move_nm(axis, direction * settings.step_nm)
            QThread.msleep(50)

            current_power = self._read_power(settings.samples_per_point)
            self.progress_updated.emit(f"Climbing {axis}-axis...", current_power)

            # Use a small tolerance for comparison to handle noise
            if current_power > (max_power + 0.01):  # Gained significant power
                max_power = current_power
                max_power_pos_v = piezo.get_voltage(axis)
                logger.debug(f"New max power: {max_power:.3f} dBm at {max_power_pos_v:.3f} V")
            else:  # Power did not increase significantly
                if not has_flipped_direction:
                    # First time power drops, flip direction and go back to the best spot
                    logger.debug("Power dropped. Flipping direction.")
                    direction = -1
                    has_flipped_direction = True
                    # Go back to the last known max before starting in the new direction
                    piezo.set_voltage(axis, max_power_pos_v)
                    QThread.msleep(50)
                else:
                    # We've already flipped direction and power dropped again. We are done.
                    logger.debug("Power dropped in second direction. Climb finished.")
                    break  # Exit the while loop

        # After the loop, move to the absolute best position found.
        logger.info(f"Finished {axis}-axis climb. Setting to best position: {max_power_pos_v:.3f} V")
        piezo.set_voltage(axis, max_power_pos_v)
        QThread.msleep(50)

        final_power = self._read_power(settings.samples_per_point)
        self.progress_updated.emit(f"Finished {axis}-axis climb. Power: {final_power:.2f} dBm", final_power)

    def _read_power(self, num_samples: int) -> float:
        """Reads power from the CT400, optionally averaging."""
        if num_samples <= 1:
            return self.ct400.get_all_powers().detectors[Detector.DE_1]

        samples = []
        for _ in range(num_samples):
            if not self._is_running:
                return -999
            samples.append(self.ct400.get_all_powers().detectors[Detector.DE_1])
            QThread.msleep(20)
        return np.mean(samples)

    @Slot(MappingSettings)
    def run_mapping(self, settings: MappingSettings):
        """Performs a 2D raster scan to generate a power map."""
        self._is_running = True
        logger.info(f"Starting power mapping with settings: {settings}")

        try:
            piezo_to_use = self.piezo_left if settings.stage_to_map == "left" else self.piezo_right

            # --- NEW: Get voltage limits ---
            x_min_v = piezo_to_use.get_min_voltage("x")
            x_max_v = piezo_to_use.get_max_voltage("x")
            y_min_v = piezo_to_use.get_min_voltage("y")
            y_max_v = piezo_to_use.get_max_voltage("y")
            logger.info(f"Voltage Limits: X=[{x_min_v:.2f}, {x_max_v:.2f}], Y=[{y_min_v:.2f}, {y_max_v:.2f}]")

            x_range_nm = np.arange(settings.x_min_nm, settings.x_max_nm + 1, settings.x_step_nm)
            y_range_nm = np.arange(settings.y_min_nm, settings.y_max_nm + 1, settings.y_step_nm)
            power_grid = np.zeros((len(x_range_nm), len(y_range_nm)))

            total_points = len(x_range_nm) * len(y_range_nm)
            points_done = 0

            initial_x_v = piezo_to_use.get_voltage("x")
            initial_y_v = piezo_to_use.get_voltage("y")
            logger.info(f"Mapping started. Initial position: X={initial_x_v:.3f}V, Y={initial_y_v:.3f}V")

            for i, x_offset_nm in enumerate(x_range_nm):
                for j, y_offset_nm in enumerate(y_range_nm):
                    if not self._is_running:
                        raise InterruptedError("Mapping cancelled by user.")

                    volts_per_nm = piezo_to_use.VOLTS_PER_NM
                    target_x_v = initial_x_v + (x_offset_nm * volts_per_nm)
                    target_y_v = initial_y_v + (y_offset_nm * volts_per_nm)

                    # --- FIX: Clamp the target voltage to the allowed range ---
                    clamped_x_v = max(x_min_v, min(x_max_v, target_x_v))
                    clamped_y_v = max(y_min_v, min(y_max_v, target_y_v))

                    if abs(clamped_x_v - target_x_v) > 0.001 or abs(clamped_y_v - target_y_v) > 0.001:
                        logger.warning(
                            f"Target voltage out of range. Clamping. Target:({target_x_v:.2f}, {target_y_v:.2f}), Clamped:({clamped_x_v:.2f}, {clamped_y_v:.2f})"
                        )

                    piezo_to_use.set_voltage("x", clamped_x_v)
                    piezo_to_use.set_voltage("y", clamped_y_v)
                    QThread.msleep(50)

                    power_val = self._read_power(settings.samples_per_point)
                    power_grid[i, j] = power_val

                    points_done += 1
                    progress_percent = int(100 * points_done / total_points)
                    self.mapping_progress.emit(progress_percent, total_points)

            logger.info("Mapping scan complete. Returning to initial position.")
            piezo_to_use.set_voltage("x", initial_x_v)
            piezo_to_use.set_voltage("y", initial_y_v)

            power_grid_mw = 10 ** (power_grid / 10.0)
            x_coords_um = x_range_nm / 1000.0
            y_coords_um = y_range_nm / 1000.0

            self.mapping_finished.emit(x_coords_um, y_coords_um, power_grid_mw)

        except InterruptedError as e:
            logger.warning(f"Mapping process interrupted: {e}")
            if "initial_x_v" in locals() and "piezo_to_use" in locals():
                piezo_to_use.set_voltage("x", initial_x_v)
                piezo_to_use.set_voltage("y", initial_y_v)
        except Exception as e:
            logger.exception("An error occurred during mapping.")
            self.error_occurred.emit(f"Error during mapping: {e}")
        finally:
            self._is_running = False


class InterruptedError(Exception):
    """Custom exception for user-cancellations."""

    pass
