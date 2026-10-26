"""Shared mutable scan display settings without Qt or hardware dependencies."""


class ScanSettings:
    def __init__(self):
        self.resolution: str = "N/A"
        self.motor_speed: str = "N/A"
        self.laser_power: str = "N/A"
        self.power_unit: str = ""
