"""Driver-free contract tests for the two operator-invoked discovery scripts."""

from __future__ import annotations

import builtins
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

import cameras_info
import find_piezos


def test_camera_inventory_import_and_help_do_not_import_vendor_sdk(monkeypatch, capsys):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "vmbpy":
            raise AssertionError("Import/help must not load VmbPy")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(cameras_info)
    with pytest.raises(SystemExit) as exc:
        cameras_info.main(["--help"])
    assert exc.value.code == 0
    assert "camera" in capsys.readouterr().out.lower()


def test_camera_inventory_lists_without_opening_or_streaming(monkeypatch, capsys):
    events = []

    class FakeVmbSystem:
        def __enter__(self):
            events.append("enter")
            return self

        def get_all_cameras(self):
            events.append("list")
            return ["DEV_TEST_TOP", "DEV_TEST_SIDE"]

        def __exit__(self, *_args):
            events.append("exit")

    system = FakeVmbSystem()
    fake_api = ModuleType("vmbpy")
    fake_api.VmbSystem = SimpleNamespace(get_instance=lambda: system)
    monkeypatch.setitem(sys.modules, "vmbpy", fake_api)

    assert cameras_info.main([]) == 0
    assert capsys.readouterr().out.splitlines() == ["DEV_TEST_TOP", "DEV_TEST_SIDE"]
    assert events == ["enter", "list", "exit"]


def test_missing_vmbpy_returns_clear_error_without_simulated_fallback(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "vmbpy", None)
    assert cameras_info.main([]) == 1
    assert "Camera inventory unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("COM3,COM5,", ["COM3", "COM5"]),
        ("COM4,1234567890,junk,", ["COM4", "1234567890"]),
        ("com9,COM10", ["com9", "COM10"]),
        ("unrecognized,,12345", []),
        ("", []),
    ],
)
def test_piezo_identifier_parser_preserves_legacy_rules(raw, expected):
    assert find_piezos.parse_device_identifiers(raw) == expected


def test_piezo_import_and_help_never_load_vendor_dll(monkeypatch, capsys):
    def unexpected_dll_load(_path):
        raise AssertionError("Import/help must not load the vendor DLL")

    monkeypatch.setattr(find_piezos.cdll, "LoadLibrary", unexpected_dll_load)
    importlib.reload(find_piezos)
    with pytest.raises(SystemExit) as exc:
        find_piezos.main(["--help"])
    assert exc.value.code == 0
    assert "--dll" in capsys.readouterr().out


def test_missing_piezo_dll_returns_error_without_loading_library(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        find_piezos.cdll,
        "LoadLibrary",
        lambda _path: pytest.fail("Absent DLL must never be loaded"),
    )
    assert find_piezos.main(["--dll", str(tmp_path / "not-installed.dll")]) == 1
    assert "DLL was not found" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("raw_output", "result_code", "exit_code", "message"),
    [
        (b"COM3,COM5,1234567890,", 3, 0, "Device #3:"),
        (b"", 0, 0, "No Piezo devices were found"),
        (b"unknown,invalid,", 2, 1, "Could not parse"),
        (b"", -5, 1, "error code: -5"),
        (bytes([0xFF]), 1, 1, "undecodable"),
    ],
)
def test_piezo_inventory_only_calls_vendor_list(tmp_path, monkeypatch, capsys, raw_output, result_code, exit_code, message):
    dll_path = tmp_path / "vendor.dll"
    dll_path.write_bytes(b"test-only stand-in")
    calls = []

    class VendorList:
        argtypes = None
        restype = None

        def __call__(self, buffer, size):
            calls.append(("List", size))
            buffer.value = raw_output
            return result_code

    vendor_list = VendorList()

    def fake_load(path):
        calls.append(("LoadLibrary", path))
        return SimpleNamespace(List=vendor_list)

    monkeypatch.setattr(find_piezos.cdll, "LoadLibrary", fake_load)

    assert find_piezos.main(["--dll", str(dll_path)]) == exit_code
    output = capsys.readouterr().out
    assert message in output
    assert calls == [("LoadLibrary", str(dll_path)), ("List", 10240)]
    assert vendor_list.argtypes is not None
    assert vendor_list.restype is not None
