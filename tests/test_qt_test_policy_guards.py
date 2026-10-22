"""Enforce the in-process Qt application ownership rule for future pytest modules.

Tests that need to construct a QApplication from scratch must use an isolated
subprocess (typically with Python source supplied as a string). The main
pytest process uses pytest-qt's session qapp and tests/conftest.py lifecycle.
"""

from __future__ import annotations

import ast
from pathlib import Path

QT_APPLICATION_NAMES = frozenset({"QApplication", "QGuiApplication", "QCoreApplication"})


def _in_process_app_constructors(source: str) -> list[int]:
    tree = ast.parse(source)
    aliases = set(QT_APPLICATION_NAMES)
    # Catch normal Qt application imports with explicit aliasing as well.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in {
            "PySide6.QtWidgets",
            "PySide6.QtGui",
            "PySide6.QtCore",
        }:
            continue
        for name in node.names:
            if name.name in QT_APPLICATION_NAMES:
                aliases.add(name.asname or name.name)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id in aliases:
            offenders.append(node.lineno)
        elif isinstance(function, ast.Attribute) and function.attr in QT_APPLICATION_NAMES:
            # Qualifying the constructor through QtWidgets/QtCore does not
            # make a second application safe in the existing pytest process.
            offenders.append(node.lineno)
    return sorted(set(offenders))


def test_new_qt_tests_reuse_pytest_qapp_instead_of_constructing_another_application() -> None:
    tests = Path(__file__).resolve().parent
    offenders = []
    modules = {path for path in tests.rglob("test_*.py")}
    modules.update(tests.rglob("*_test.py"))
    for path in sorted(modules):
        lines = _in_process_app_constructors(path.read_text(encoding="utf-8"))
        if lines:
            relative = path.relative_to(tests)
            offenders.append(f"{relative}:{','.join(map(str, lines))}")
    assert not offenders, (
        "Never create QApplication/QGuiApplication/QCoreApplication in the pytest process. "
        "Use the qapp and qtbot fixtures or a bounded, driver-free subprocess: " + "; ".join(offenders)
    )


def test_qt_application_policy_rejects_plain_and_aliased_constructors() -> None:
    example = (
        "from PySide6.QtWidgets import QApplication as App\n"
        "import PySide6.QtGui as Gui\n"
        "App([])\n"
        "Gui.QGuiApplication([])\n"
        "QCoreApplication([])\n"
    )
    assert _in_process_app_constructors(example) == [3, 4, 5]


def test_qt_application_policy_allows_fixture_access_and_subprocess_source() -> None:
    example = (
        "from PySide6.QtWidgets import QApplication\n"
        "def test_uses_existing_app(qapp):\n"
        "    assert QApplication.instance() is qapp\n"
        "child = 'from PySide6.QtWidgets import QApplication; QApplication([])'\n"
    )
    assert _in_process_app_constructors(example) == []
