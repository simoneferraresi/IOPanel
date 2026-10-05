import inspect
from abc import abstractmethod

import pytest

from config_model import AppConfig
from ui.control_panel import (
    BaseControlPanel,
    CT400ControlPanel,
    HistogramControlPanel,
    ScanSettings,
)


def test_abstract_control_panel_is_recognized_and_keeps_abstract_members():
    assert inspect.isabstract(BaseControlPanel)
    abstract_methods = {
        name for name, value in vars(BaseControlPanel).items() if getattr(value, "__isabstractmethod__", False)
    }
    assert abstract_methods == {
        "_force_stop",
        "_get_configurable_widgets",
        "_get_main_action_button",
        "_init_subclass_ui",
        "is_busy",
    }
    # ABCMeta normally publishes this set. Shiboken does not in this runtime.
    if hasattr(BaseControlPanel, "__abstractmethods__"):
        assert BaseControlPanel.__abstractmethods__


def test_abstract_base_control_panel_cannot_be_instantiated():
    with pytest.raises(TypeError, match="BaseControlPanel") as exc_info:
        BaseControlPanel(None, AppConfig())

    message = str(exc_info.value)
    assert "_force_stop" in message
    assert "_get_configurable_widgets" in message
    assert "_get_main_action_button" in message
    assert "_init_subclass_ui" in message
    assert "is_busy" in message


def test_incomplete_subclass_cannot_be_instantiated():
    class IncompleteControlPanel(BaseControlPanel):
        @abstractmethod
        def _init_subclass_ui(self): ...

    assert inspect.isabstract(IncompleteControlPanel)
    with pytest.raises(TypeError, match="IncompleteControlPanel") as exc_info:
        IncompleteControlPanel(None, AppConfig())

    assert "_init_subclass_ui" in str(exc_info.value)


def test_concrete_ct400_panel_remains_constructible(qtbot):
    assert not inspect.isabstract(CT400ControlPanel)
    assert not inspect.isabstract(HistogramControlPanel)

    panel = CT400ControlPanel(ScanSettings(), None, AppConfig())
    qtbot.addWidget(panel)

    assert not inspect.isabstract(type(panel))
