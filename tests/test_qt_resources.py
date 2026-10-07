"""Checks for the source and compiled Qt resource bundle."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtCore import QFile, QIODevice

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_COLLECTION = REPOSITORY_ROOT / "resources" / "resources.qrc"
REQUIRED_RESOURCE_ENTRIES = {
    ("/icons", "check.svg", "icons/check.svg"),
    ("/icons", "chevron-down.svg", "icons/chevron-down.svg"),
    ("/icons", "connect.svg", "icons/connect.svg"),
    ("/icons", "refresh.svg", "icons/refresh.svg"),
    ("/icons", "record.svg", "icons/record.svg"),
    ("/icons", "eraser.svg", "icons/eraser.svg"),
    ("/icons", "camera.svg", "icons/camera.svg"),
    ("/icons", "camera-white.svg", "icons/camera-white.svg"),
    ("/icons", "disconnect.svg", "icons/disconnect.svg"),
    ("/icons", "exit.svg", "icons/exit.svg"),
    ("/icons", "laser.svg", "icons/laser.svg"),
    ("/icons", "play.svg", "icons/play.svg"),
    ("/icons", "radio-checked.svg", "icons/radio-checked.svg"),
    ("/icons", "spinner.svg", "icons/spinner.svg"),
    ("/icons", "save.svg", "icons/save.svg"),
    ("/icons", "snowflake.svg", "icons/snowflake.svg"),
    ("/icons", "stop-circle.svg", "icons/stop-circle.svg"),
    ("/icons", "stop.svg", "icons/stop.svg"),
    ("/fonts", "Geist-Variable.ttf", "fonts/Geist-Variable.ttf"),
    ("/fonts", "GeistMono-Variable.ttf", "fonts/GeistMono-Variable.ttf"),
    ("/fonts", "OFL.txt", "fonts/OFL.txt"),
}


def test_resource_collection_sources_exist() -> None:
    root = ET.parse(RESOURCE_COLLECTION).getroot()
    declared_entries = [
        (qresource.get("prefix"), entry.get("alias"), entry.text or "")
        for qresource in root.findall("./qresource")
        for entry in qresource.findall("./file")
    ]
    resource_entries = set(declared_entries)
    source_paths = [RESOURCE_COLLECTION.parent / source for _prefix, _alias, source in declared_entries]

    assert REQUIRED_RESOURCE_ENTRIES <= resource_entries
    assert len(set(source_paths)) == len(source_paths)
    assert all(path.is_file() for path in source_paths)


def test_compiled_resource_aliases_are_registered(qapp) -> None:
    importlib.import_module("resources.resources_rc")

    for alias in (
        ":/icons/laser.svg",
        ":/icons/play.svg",
        ":/icons/camera.svg",
        ":/icons/camera-white.svg",
        ":/icons/record.svg",
        ":/fonts/Geist-Variable.ttf",
        ":/fonts/GeistMono-Variable.ttf",
        ":/fonts/OFL.txt",
    ):
        resource_file = QFile(alias)
        assert resource_file.open(QIODevice.OpenModeFlag.ReadOnly), alias
        try:
            assert not resource_file.readAll().isEmpty(), alias
        finally:
            resource_file.close()


def test_resource_compiler_smoke(tmp_path: Path) -> None:
    compiler = shutil.which("pyside6-rcc")
    if compiler is None:
        pytest.skip("pyside6-rcc is not available in this environment")

    generated_module = tmp_path / "resources_smoke_rc.py"
    subprocess.run(
        [compiler, str(RESOURCE_COLLECTION), "-o", str(generated_module)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    verification = textwrap.dedent(
        """
        import importlib.util
        import sys
        from PySide6.QtCore import QFile, QIODevice

        spec = importlib.util.spec_from_file_location("resources_smoke_rc", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for alias in (
            ":/icons/laser.svg",
            ":/icons/play.svg",
            ":/icons/camera.svg",
            ":/icons/camera-white.svg",
            ":/icons/record.svg",
            ":/fonts/Geist-Variable.ttf",
            ":/fonts/GeistMono-Variable.ttf",
            ":/fonts/OFL.txt",
        ):
            resource = QFile(alias)
            assert resource.open(QIODevice.OpenModeFlag.ReadOnly), alias
            try:
                assert not resource.readAll().isEmpty(), alias
            finally:
                resource.close()
        """
    )
    subprocess.run(
        [sys.executable, "-c", verification, str(generated_module)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
