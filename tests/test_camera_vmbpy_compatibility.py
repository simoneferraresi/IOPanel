"""Hardware-independent checks for the optional VmbPy adapter boundary."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _run_isolated_import(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_camera_adapter_imports_expected_vmbpy_surface_from_stub() -> None:
    _run_isolated_import(
        """
        import sys
        import types

        vmbpy = types.ModuleType("vmbpy")
        for name in (
            "COLOR_PIXEL_FORMATS", "MONO_PIXEL_FORMATS", "OPENCV_PIXEL_FORMATS",
            "Camera", "Frame", "FrameStatus", "PixelFormat", "Stream",
            "VmbCameraError", "VmbSystem", "VmbSystemError", "intersect_pixel_formats",
        ):
            setattr(vmbpy, name, () if name.endswith("FORMATS") else type(name, (), {}))
        sys.modules["vmbpy"] = vmbpy

        from hardware.camera import VIMBA_AVAILABLE, VmbSystem

        assert VIMBA_AVAILABLE
        assert VmbSystem is vmbpy.VmbSystem
        """
    )


def test_vmbpy_missing_runtime_is_treated_as_optional_unavailability() -> None:
    _run_isolated_import(
        """
        import importlib.abc
        import importlib.util
        import sys
        import types

        class VmbSystemError(Exception):
            pass

        VmbSystemError.__module__ = "vmbpy.error"
        error_module = types.ModuleType("vmbpy.error")
        error_module.VmbSystemError = VmbSystemError
        sys.modules["vmbpy.error"] = error_module

        class BrokenVmbPyLoader:
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                raise VmbSystemError("matching VmbC runtime unavailable")

        class BrokenVmbPyFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "vmbpy":
                    return importlib.util.spec_from_loader(fullname, BrokenVmbPyLoader())

        sys.meta_path.insert(0, BrokenVmbPyFinder())

        from hardware.camera import VIMBA_AVAILABLE, VIMBA_IMPORT_ERROR

        assert not VIMBA_AVAILABLE
        assert isinstance(VIMBA_IMPORT_ERROR, VmbSystemError)
        """
    )


def test_missing_vmbpy_import_is_treated_as_optional_unavailability() -> None:
    _run_isolated_import(
        """
        import importlib.abc
        import importlib.util
        import sys

        class MissingVmbPyLoader:
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                raise ImportError("VmbPy is not installed")

        class MissingVmbPyFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "vmbpy":
                    return importlib.util.spec_from_loader(fullname, MissingVmbPyLoader())

        sys.meta_path.insert(0, MissingVmbPyFinder())

        from hardware.camera import VIMBA_AVAILABLE, VIMBA_IMPORT_ERROR

        assert not VIMBA_AVAILABLE
        assert isinstance(VIMBA_IMPORT_ERROR, ImportError)
        """
    )


def test_unexpected_vmbpy_import_exceptions_are_not_suppressed() -> None:
    _run_isolated_import(
        """
        import importlib.abc
        import importlib.util
        import sys

        class BrokenVmbPyLoader:
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                raise RuntimeError("unexpected adapter import defect")

        class BrokenVmbPyFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "vmbpy":
                    return importlib.util.spec_from_loader(fullname, BrokenVmbPyLoader())

        sys.meta_path.insert(0, BrokenVmbPyFinder())
        try:
            import hardware.camera
        except RuntimeError as error:
            assert str(error) == "unexpected adapter import defect"
        else:
            raise AssertionError("Unexpected VmbPy import error was suppressed")
        """
    )
