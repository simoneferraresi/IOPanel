"""Passive USB/serial identity resolution for configured piezo controllers.

A USB serial belongs to the Windows/USB interface, and is not necessarily the
Thorlabs controller's native GetId serial. No DLL or COM port is opened here.
"""

from dataclasses import dataclass

from serial.tools import list_ports


@dataclass(frozen=True)
class PiezoPortInfo:
    device: str
    description: str
    manufacturer: str
    product: str
    usb_serial: str
    vid: int | None
    pid: int | None
    hwid: str


def enumerate_piezo_ports() -> tuple[PiezoPortInfo, ...]:
    """Read Windows/USB metadata without opening or claiming any serial port."""
    return tuple(
        sorted(
            (
                PiezoPortInfo(
                    device=port.device,
                    description=port.description or "",
                    manufacturer=port.manufacturer or "",
                    product=port.product or "",
                    usb_serial=port.serial_number or "",
                    vid=port.vid,
                    pid=port.pid,
                    hwid=port.hwid or "",
                )
                for port in list_ports.comports()
            ),
            key=lambda info: info.device,
        )
    )


def resolve_piezo_port(
    ports: tuple[PiezoPortInfo, ...],
    *,
    configured_port: str = "",
    usb_serial: str = "",
) -> str | None:
    """Return one unambiguous port; never fall back to COM when a USB ID is bound.

    Missing and duplicate serials deliberately fail closed instead of guessing
    which physical stage would be controlled.
    """
    if usb_serial.strip():
        matches = [port.device for port in ports if port.usb_serial and port.usb_serial.casefold() == usb_serial.strip().casefold()]
    elif configured_port.strip():
        matches = [port.device for port in ports if port.device.casefold() == configured_port.strip().casefold()]
    else:
        matches = []
    return matches[0] if len(matches) == 1 else None


def unique_usb_serials(ports: tuple[PiezoPortInfo, ...]) -> set[str]:
    """USB IDs observed exactly once, eligible for operator-confirmed binding."""
    counts: dict[str, int] = {}
    for port in ports:
        if port.usb_serial:
            key = port.usb_serial.casefold()
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count == 1}
