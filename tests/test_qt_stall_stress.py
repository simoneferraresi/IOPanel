"""Workflow-only test that repeats the safe native-Windows Qt lifecycle."""

import os

import pytest


@pytest.mark.skipif(
    "IOPANEL_QT_STRESS_CONFIG" not in os.environ,
    reason="run only through the opt-in externally supervised Qt stress workflow",
)
def test_repeated_simulated_mainwindow_lifecycle():
    from tools.qt_stall_stress_child import main

    assert main() == 0
