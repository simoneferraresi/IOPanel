import logging
import threading
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ui.log_console import BoundedLogHandler, LogConsole, install_log_handler


def test_bounded_handler_formats_levels_and_tracebacks():
    handler = BoundedLogHandler(capacity=3)
    logger = logging.Logger("LabApp.test")  # noqa: LOG001
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    for level in (logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        logger.log(level, "message %s", level)
    try:
        raise ValueError("trace detail")
    except ValueError:
        logger.exception("operation failed")
    entries, latest = handler.snapshot()
    assert latest == 5
    assert len(entries) == 3
    assert "operation failed" in entries[-1].text
    assert "Traceback" in entries[-1].text
    assert "ValueError: trace detail" in entries[-1].text


def test_handler_installed_only_once():
    logger = logging.Logger("LabApp.test.once")  # noqa: LOG001
    first = install_log_handler(logger)
    second = install_log_handler(logger)
    assert first is second
    assert logger.handlers.count(first) == 1


def test_console_handler_does_not_interfere_with_file_logging(tmp_path: Path):
    logger = logging.Logger("LabApp.test.file")  # noqa: LOG001
    log_path = tmp_path / "application.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    bridge = BoundedLogHandler()
    logger.addHandler(file_handler)
    logger.addHandler(bridge)
    logger.setLevel(logging.DEBUG)
    logger.error("file remains authoritative")
    file_handler.close()
    assert "file remains authoritative" in log_path.read_text(encoding="utf-8")
    assert "file remains authoritative" in bridge.snapshot()[0][-1].text


def test_concurrent_delivery_and_gui_filter_search_pause_clear(qtbot):
    logger = logging.Logger("LabApp.test.console")  # noqa: LOG001
    logger.setLevel(logging.DEBUG)
    console = LogConsole(logger)
    qtbot.addWidget(console)
    console.show()

    def emit_messages():
        for index in range(2000):
            logger.info("worker record %d", index)

    worker = threading.Thread(target=emit_messages)
    worker.start()
    worker.join(timeout=3)
    assert not worker.is_alive()
    qtbot.waitUntil(lambda: "worker record 1999" in console.text.toPlainText(), timeout=3000)
    assert len(console._visible_entries) <= 5000

    logger.warning("filter candidate warning")
    logger.error("filter candidate error")
    qtbot.waitUntil(lambda: "filter candidate error" in console.text.toPlainText(), timeout=2000)
    console.level_filter.setCurrentText("ERROR")
    assert "filter candidate error" in console.text.toPlainText()
    assert "filter candidate warning" not in console.text.toPlainText()
    console.level_filter.setCurrentText("All")

    console.search.setText("worker record 19")
    assert "worker record 19" in console.text.toPlainText()
    assert "worker record 2 |" not in console.text.toPlainText()
    console.search.clear()
    console.clear_display()
    assert console.text.toPlainText() == ""
    logger.error("later error")
    qtbot.waitUntil(lambda: "later error" in console.text.toPlainText(), timeout=2000)
    console._toggle_pause()
    logger.error("paused error")
    qtbot.wait(150)
    assert "paused error" not in console.text.toPlainText()
    console._toggle_pause()
    assert "paused error" in console.text.toPlainText()


def test_copy_visible_uses_qt_clipboard(qtbot):
    console = LogConsole(logging.Logger("LabApp.test.copy"))  # noqa: LOG001
    qtbot.addWidget(console)
    console.text.setPlainText("visible message")
    console.copy_visible()
    assert QApplication.clipboard().text() == "visible message"


def test_console_stops_gui_polling_during_teardown(qtbot):
    console = LogConsole(logging.Logger("LabApp.test.teardown"))  # noqa: LOG001
    qtbot.addWidget(console)
    assert console._timer.isActive()
    console.stop_updates()
    assert not console._timer.isActive()


def test_diagnostics_action_uses_api_and_displays_report(qtbot, monkeypatch):
    from tools import check_environment

    calls = []
    monkeypatch.setattr(check_environment, "collect_diagnostics", lambda: calls.append("collect") or {})
    monkeypatch.setattr(
        check_environment,
        "render_report",
        lambda _report: "[MISSING_OPTIONAL] MATLAB Engine\n[NOT_CHECKED] camera runtime",
    )
    console = LogConsole(logging.Logger("LabApp.test.diagnostics"))  # noqa: LOG001
    qtbot.addWidget(console)
    console.run_diagnostics()
    assert calls == ["collect"]
    qtbot.waitUntil(lambda: "MISSING_OPTIONAL" in console.text.toPlainText(), timeout=2000)
    assert "NOT_CHECKED" in console.text.toPlainText()
