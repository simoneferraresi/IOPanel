"""Safe regression coverage for identity-bound passive piezo discovery."""

from PySide6.QtWidgets import QApplication

from config_model import InstrumentsConfig
from hardware import piezo_init_worker
from hardware.piezo_ports import PiezoPortInfo, resolve_piezo_port, unique_usb_serials
from ui.piezo_configuration_dialog import PiezoConfigurationDialog


def port(name, serial="", description="Thorlabs MDT693B"):
    return PiezoPortInfo(
        device=name,
        description=description,
        manufacturer="Thorlabs",
        product="MDT693B",
        usb_serial=serial,
        vid=0x1313,
        pid=0x1003,
        hwid=f"USB VID:PID=1313:1003 SER={serial}",
    )


def test_binding_follows_usb_serial_when_com_port_changes():
    ports = (port("COM17", "ABC"), port("COM5", "XYZ"))
    assert resolve_piezo_port(ports, configured_port="COM3", usb_serial="abc") == "COM17"
    assert resolve_piezo_port(ports, configured_port="COM5", usb_serial="XYZ") == "COM5"


def test_bound_identity_never_falls_back_to_com_if_missing_or_duplicated():
    ports = (port("COM3", "OTHER"), port("COM4", "SERIAL"), port("COM7", "serial"))
    assert resolve_piezo_port(ports, configured_port="COM3", usb_serial="MISSING") is None
    assert resolve_piezo_port(ports, configured_port="COM4", usb_serial="SERIAL") is None
    assert "serial" not in unique_usb_serials(ports)


def test_legacy_com_fallback_is_case_insensitive_only_without_usb_binding():
    ports = (port("COM3"), port("COM4"))
    assert resolve_piezo_port(ports, configured_port="com3") == "COM3"
    assert resolve_piezo_port(ports, configured_port="COM5") is None
    assert resolve_piezo_port(ports) is None


def test_worker_resolves_ports_without_native_open_and_prevents_duplicate_claim(monkeypatch, tmp_path):
    fake_dll = tmp_path / "fake.dll"
    fake_dll.touch()
    calls = []

    class StubPiezo:
        def __init__(self, _dll):
            calls.append("wrapper created")

        def is_connected(self):
            return False

    monkeypatch.setattr(piezo_init_worker, "PiezoController", StubPiezo)
    monkeypatch.setattr(
        piezo_init_worker,
        "enumerate_piezo_ports",
        lambda: (port("COM3", "LEFT"), port("COM4", "RIGHT")),
    )
    config = InstrumentsConfig(
        piezo_dll_path=str(fake_dll),
        piezo_left_serial="COM5",
        piezo_right_serial="COM6",
        piezo_left_usb_serial="LEFT",
        piezo_right_usb_serial="RIGHT",
    )
    worker = piezo_init_worker.PiezoInitWorker(config)
    observed = []
    worker.piezos_initialized.connect(lambda left, right: observed.append((left, right)))
    worker.run()
    assert len(observed) == 1
    assert observed[0][0].discovered_port == "COM3"
    assert observed[0][1].discovered_port == "COM4"
    assert calls == ["wrapper created", "wrapper created"]

    # Accidentally assigning one physical USB identity to both sides must
    # fail closed rather than sharing a native controller across worker roles.
    worker = piezo_init_worker.PiezoInitWorker(config.model_copy(update={"piezo_right_usb_serial": "LEFT"}))
    observed.clear()
    worker.piezos_initialized.connect(lambda left, right: observed.append((left, right)))
    worker.run()
    assert observed == [(None, None)]


def test_passive_configuration_dialog_refuses_duplicate_or_unverified_bindings(qtbot, monkeypatch):
    monkeypatch.setattr(
        "ui.piezo_configuration_dialog.enumerate_piezo_ports",
        lambda: (port("COM3", "ONE"), port("COM4", "TWO"), port("COM5", "TWO")),
    )
    dialog = PiezoConfigurationDialog(InstrumentsConfig(piezo_left_serial="COM3", piezo_right_serial="COM4"))
    qtbot.addWidget(dialog)
    assert dialog.port_table.rowCount() == 3
    # Duplicate TWO is not offered for automatic binding.
    assert dialog.left_combo.count() == 2
    assert dialog.copy_button.isEnabled() is False
    dialog.left_combo.setCurrentIndex(1)
    assert not dialog.copy_button.isEnabled()
    dialog.verified.setChecked(True)
    assert dialog.copy_button.isEnabled()
    dialog.copy_button.click()
    assert "piezo_left_usb_serial = ONE" in QApplication.clipboard().text()
    assert "piezo_right_usb_serial = " in QApplication.clipboard().text()
    dialog.close()
