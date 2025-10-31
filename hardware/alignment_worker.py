# hardware/alignment_worker.py

import logging
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from hardware.ct400_types import Detector, Enable, LaserInput
from hardware.interfaces import AbstractCT400
from hardware.piezo import PiezoController

logger = logging.getLogger("LabApp.AlignmentWorker")


@dataclass
class SpiralSearchSettings:
    """Holds parameters for the coarse spiral search."""

    radius_um: float = 5.0  # Search radius in micrometers
    step_um: float = 0.5  # The distance between points on the spiral path in micrometers


@dataclass
class AlignmentSettings:
    """Holds all parameters for an alignment task."""

    # Laser settings
    laser_wavelength_nm: float
    laser_power: float
    power_unit: str  # "mW" or "dBm"
    input_port: int

    # Algorithm settings
    iterations: int
    step_nm: float
    samples_per_point: int
    coupling_type: str  # "butt" or "top"
    settling_time_ms: int = 100


@dataclass
class MappingSettings:
    """Holds all parameters for a 2D power mapping task."""

    # Laser settings (needed to turn on the laser)
    laser_wavelength_nm: float
    laser_power: float
    power_unit: str  # "mW" or "dBm"
    input_port: int

    # Mapping geometry
    x_min_nm: int
    x_max_nm: int
    x_step_nm: int
    y_min_nm: int
    y_max_nm: int
    y_step_nm: int

    # Algorithm settings
    samples_per_point: int
    stage_to_map: str  # "left" or "right"
    settling_time_ms: int = 100


class AlignmentWorker(QObject):
    """
    Worker to perform iterative optical alignment using piezo stages and a power meter.
    Runs in a separate thread to avoid blocking the GUI.
    """

    progress_updated = Signal(str, float, bool)
    alignment_finished = Signal(str, float, float, object, object)
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

    @Slot(AlignmentSettings, SpiralSearchSettings)
    def run_spiral_alignment(self, align_settings: AlignmentSettings, spiral_settings: SpiralSearchSettings):
        """
        Performs a coarse spiral search to find the approximate peak, then runs
        a fine-grained hill-climbing alignment from that point.
        """
        self._is_running = True
        logger.info("Starting spiral alignment workflow...")
        try:
            self._prepare_laser(align_settings)

            # Phase 1: Coarse Spiral Search on BOTH stages
            for piezo, name in [(self.piezo_left, "Left"), (self.piezo_right, "Right")]:
                if not self._is_running:
                    raise InterruptedError("Cancelled during spiral search prep.")

                self.progress_updated.emit(f"Spiral Search: {name} Stage...", -999, False)
                best_x_v, best_y_v = self._find_coarse_peak_spiral(
                    piezo, spiral_settings, align_settings.samples_per_point
                )

                # Move to the best position found during the spiral search
                piezo.set_voltage("x", best_x_v)
                piezo.set_voltage("y", best_y_v)
                QThread.msleep(100)
                power = self._read_power(align_settings.samples_per_point)
                self.progress_updated.emit(f"Spiral Found Peak at {power:.2f} dBm", power, False)

            # Phase 2: Fine-grained Hill-Climbing Alignment
            # This now starts from a much better position.
            # We can re-use the existing run_alignment method's core logic.
            logger.info("Spiral search complete. Starting fine-grained alignment.")
            initial_power_fine_tune = self._read_power(align_settings.samples_per_point)

            # Run the iterative hill-climb
            for i in range(align_settings.iterations):
                if not self._is_running:
                    raise InterruptedError("Cancelled during fine alignment.")
                self.progress_updated.emit(f"Fine Tune {i + 1}/{align_settings.iterations}: Left Stage", -999, False)
                self._align_stage(self.piezo_left, align_settings)

                self.progress_updated.emit(f"Fine Tune {i + 1}/{align_settings.iterations}: Right Stage", -999, False)
                self._align_stage(self.piezo_right, align_settings)

            # Emit the final results
            final_power = self._read_power(align_settings.samples_per_point)
            final_positions = {
                "left_x": self.piezo_left.get_voltage("x"),
                "left_y": self.piezo_left.get_voltage("y"),
                "left_z": self.piezo_left.get_voltage("z"),
                "right_x": self.piezo_right.get_voltage("x"),
                "right_y": self.piezo_right.get_voltage("y"),
                "right_z": self.piezo_right.get_voltage("z"),
            }
            # For the summary, the "initial" positions for the fine-tune are what matter
            self.alignment_finished.emit(
                "Spiral Alignment successful", initial_power_fine_tune, final_power, final_positions, final_positions
            )

        except InterruptedError as e:
            logger.warning(f"Spiral alignment workflow interrupted: {e}")
            self.alignment_finished.emit("Alignment cancelled", -999, -999, None, None)
        except Exception as e:
            logger.exception("An error occurred during spiral alignment.")
            self.error_occurred.emit(f"Error during spiral alignment: {e}")
        finally:
            self._shutdown_laser(align_settings)
            self._is_running = False

    def _find_coarse_peak_spiral(
        self, piezo: PiezoController, settings: SpiralSearchSettings, samples_per_point: int
    ) -> tuple[float, float]:
        """
        Performs an expanding spiral scan to find the location of highest power.
        Returns the (voltage_x, voltage_y) of the best position found.
        """
        initial_x_v = piezo.get_voltage("x")
        initial_y_v = piezo.get_voltage("y")
        logger.info(f"Starting spiral scan on {piezo.port} from V(x,y)=({initial_x_v:.2f}, {initial_y_v:.2f})")

        max_power = -999.0
        best_x_v, best_y_v = initial_x_v, initial_y_v

        # Read initial power
        power = self._read_power(samples_per_point)
        if power > max_power:
            max_power = power

        # Spiral parameters
        radius = 0.0
        theta = 0.0
        point_num = 0

        # We spiral outwards until we reach the user-defined radius
        while radius <= settings.radius_um * 1000 and self._is_running:  # Convert radius to nm
            # Calculate the position of the next point on the spiral
            x_offset_nm = radius * np.cos(theta)
            y_offset_nm = radius * np.sin(theta)

            target_x_v = initial_x_v + (x_offset_nm * piezo.VOLTS_PER_NM)
            target_y_v = initial_y_v + (y_offset_nm * piezo.VOLTS_PER_NM)

            # Clamp to valid voltage range
            clamped_x_v = max(piezo.get_min_voltage("x"), min(piezo.get_max_voltage("x"), target_x_v))
            clamped_y_v = max(piezo.get_min_voltage("y"), min(piezo.get_max_voltage("y"), target_y_v))

            piezo.set_voltage("x", clamped_x_v)
            piezo.set_voltage("y", clamped_y_v)
            QThread.msleep(50)  # A shorter settling time is acceptable for coarse search

            power = self._read_power(samples_per_point)
            self.progress_updated.emit(f"Spiraling on {piezo.port}...", power, False)

            if power > max_power:
                max_power = power
                best_x_v, best_y_v = clamped_x_v, clamped_y_v
                logger.debug(f"New spiral max power: {max_power:.2f} dBm at V(x,y)=({best_x_v:.2f}, {best_y_v:.2f})")

            # Increment spiral parameters for the next point
            point_num += 1
            # Archimedean spiral: r = a * theta
            # To maintain constant step size 's', we need to adjust theta increment
            # d(theta) = s / sqrt(r^2 + a^2), where a = s / (2*pi)
            # For simplicity, we can use a simpler approximation that works well
            if radius > 0:
                theta += settings.step_um * 1000 / radius  # Convert step to nm
            else:
                theta += np.pi / 4  # First few steps

            radius = (settings.step_um * 1000) * theta / (2 * np.pi)

        logger.info(
            f"Spiral scan on {piezo.port} complete. Best power {max_power:.2f} dBm found at V(x,y)=({best_x_v:.2f}, {best_y_v:.2f})"
        )
        return best_x_v, best_y_v

    @Slot()
    def stop(self):
        logger.info("Alignment worker stop requested.")
        self._is_running = False

    def _get_laser_power_mw(self, power: float, unit: str) -> float:
        """Helper to convert power to mW."""
        if unit.lower() == "dbm":
            return 1.0 * (10 ** (power / 10.0))
        return power

    def _prepare_laser(self, settings: AlignmentSettings | MappingSettings):
        """Turns the laser on with the specified settings."""
        logger.info("Worker preparing laser for operation...")
        power_mw = self._get_laser_power_mw(settings.laser_power, settings.power_unit)
        self.ct400.cmd_laser(
            laser_input=LaserInput(settings.input_port),
            enable=Enable.ENABLE,
            wavelength=settings.laser_wavelength_nm,
            power=power_mw,
        )
        # Give the laser a moment to stabilize after enabling
        QThread.msleep(200)
        logger.info(f"Laser enabled at {settings.laser_wavelength_nm} nm, {power_mw:.2f} mW.")

    def _shutdown_laser(self, settings: AlignmentSettings | MappingSettings):
        """Turns the laser off."""
        logger.info("Worker shutting down laser...")
        power_mw = self._get_laser_power_mw(settings.laser_power, settings.power_unit)
        self.ct400.cmd_laser(
            laser_input=LaserInput(settings.input_port),
            enable=Enable.DISABLE,
            wavelength=settings.laser_wavelength_nm,
            power=power_mw,  # Power/wavelength args are often required even for disable
        )
        logger.info("Laser disabled.")

    @Slot(AlignmentSettings)
    def run_alignment(self, settings: AlignmentSettings):
        self._is_running = True
        initial_power = -999.0
        try:
            # --- CAPTURE INITIAL POSITIONS ---
            initial_positions = {
                "left_x": self.piezo_left.get_voltage("x"),
                "left_y": self.piezo_left.get_voltage("y"),
                "left_z": self.piezo_left.get_voltage("z"),
                "right_x": self.piezo_right.get_voltage("x"),
                "right_y": self.piezo_right.get_voltage("y"),
                "right_z": self.piezo_right.get_voltage("z"),
            }

            self._prepare_laser(settings)
            initial_power = self._read_power(settings.samples_per_point)

            for i in range(settings.iterations):
                if not self._is_running:
                    raise InterruptedError("Alignment cancelled by user.")

                # --- FIX IS HERE ---
                self.progress_updated.emit(
                    f"Iteration {i + 1}/{settings.iterations}: Aligning Left Stage...", -999, False
                )
                self._align_stage(self.piezo_left, settings)

                if not self._is_running:
                    raise InterruptedError("Alignment cancelled by user.")

                # --- AND FIX IS HERE ---
                self.progress_updated.emit(
                    f"Iteration {i + 1}/{settings.iterations}: Aligning Right Stage...", -999, False
                )
                self._align_stage(self.piezo_right, settings)

            final_power = self._read_power(settings.samples_per_point)

            # Gather final positions
            final_positions = {
                "left_x": self.piezo_left.get_voltage("x"),
                "left_y": self.piezo_left.get_voltage("y"),
                "left_z": self.piezo_left.get_voltage("z"),
                "right_x": self.piezo_right.get_voltage("x"),
                "right_y": self.piezo_right.get_voltage("y"),
                "right_z": self.piezo_right.get_voltage("z"),
            }

            # --- EMIT WITH NEW DATA ---
            self.alignment_finished.emit(
                "Alignment successful", initial_power, final_power, initial_positions, final_positions
            )

        except InterruptedError:
            # Provide None for the positions on cancellation
            self.alignment_finished.emit("Alignment cancelled", -999, -999, None, None)
        except Exception as e:
            logger.exception("An error occurred during alignment.")
            self.alignment_finished.emit(f"Error: {e}", -999, -999, None, None)
        finally:
            self._shutdown_laser(settings)
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

            # --- MODIFIED EMIT ---
            # Announce which axis we are starting
            self.progress_updated.emit(f"Aligning {piezo.port} Axis-{axis}...", -999, False)

            self._climb_hill(piezo, axis, settings)

    def _climb_hill(self, piezo: PiezoController, axis: str, settings: AlignmentSettings):
        """The core single-axis alignment logic. (Corrected to not miss peaks)"""
        # Track the absolute best power and position found during this entire climb
        absolute_max_power = -999.0
        absolute_max_pos_v = piezo.get_voltage(axis)

        try:
            # 1. Read power at the starting point to initialize our maximums
            initial_power = self._read_power(settings.samples_per_point)
            absolute_max_power = initial_power
            absolute_max_pos_v = piezo.get_voltage(axis)
            logger.debug(
                f"Starting {axis}-axis climb. Initial power: {initial_power:.3f} dBm at {absolute_max_pos_v:.3f} V"
            )
            self.progress_updated.emit(f"Starting {axis}-axis climb...", absolute_max_power, False)

            # 2. Explore in both directions
            for direction in [1, -1]:
                if not self._is_running:
                    break

                # Before starting a new direction, reset to the best known position
                piezo.set_voltage(axis, absolute_max_pos_v)
                QThread.msleep(50)

                # This is the power level we are trying to beat *in the current direction*
                power_to_beat = self._read_power(settings.samples_per_point)

                logger.debug(f"Climbing in direction: {direction} from power {power_to_beat:.3f} dBm")

                while self._is_running:
                    piezo.move_nm(axis, direction * settings.step_nm)
                    QThread.msleep(settings.settling_time_ms)
                    current_power = self._read_power(settings.samples_per_point)
                    self.progress_updated.emit(f"Climbing {axis}-axis...", current_power, False)

                    # --- REVISED LOGIC ---
                    # First, ALWAYS check if this is the new absolute maximum power
                    if current_power > absolute_max_power:
                        absolute_max_power = current_power
                        absolute_max_pos_v = piezo.get_voltage(axis)
                        logger.debug(
                            f"New ABSOLUTE max power: {absolute_max_power:.3f} dBm at {absolute_max_pos_v:.3f} V"
                        )

                    # Second, decide if we should continue moving in this direction.
                    # We use a small tolerance here to avoid chasing noise indefinitely.
                    if current_power > (power_to_beat + 0.005):
                        # We made a decent improvement, so update the baseline and continue
                        power_to_beat = current_power
                    else:
                        # Power dropped or stabilized. This direction is exhausted.
                        logger.debug(f"Power stabilized in direction {direction}. Moving to next.")
                        break  # Exit the inner while loop

        finally:
            if not self._is_running:
                logger.warning(f"Climb for {axis}-axis was interrupted.")

            # 3. After exploring all directions, move to the absolute best position found
            logger.info(
                f"Finished {axis}-axis climb. Setting to absolute best position: {absolute_max_pos_v:.3f} V for max power {absolute_max_power:.3f} dBm"
            )
            piezo.set_voltage(axis, absolute_max_pos_v)
            QThread.msleep(settings.settling_time_ms)

            final_power = self._read_power(settings.samples_per_point)
            self.progress_updated.emit(f"Finished {axis}-axis climb.", final_power, True)

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
            self._prepare_laser(settings)  # <-- Turn laser ON

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
                    QThread.msleep(settings.settling_time_ms)

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
            # Important: ensure the laser is turned off even if the mapping is cancelled or fails
            if "initial_x_v" in locals() and "piezo_to_use" in locals():
                piezo_to_use.set_voltage("x", initial_x_v)
                piezo_to_use.set_voltage("y", initial_y_v)
            self._shutdown_laser(settings)  # <-- Turn laser OFF
            self._is_running = False


class InterruptedError(Exception):
    """Custom exception for user-cancellations."""

    pass
