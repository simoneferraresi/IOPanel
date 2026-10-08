import logging
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.log_console import LEVEL_COLORS, BoundedLogHandler, LogConsole, install_log_handler


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
    assert entries[-1].levelno == logging.ERROR
    assert entries[-1].levelname == "ERROR"
    assert entries[-1].logger_name == "LabApp.test"
    assert entries[-1].timestamp
    assert "operation failed" in entries[-1].text
    assert "Traceback" in entries[-1].text
    assert "ValueError: trace detail" in entries[-1].text
    assert LEVEL_COLORS["DEBUG"] == "#64748b"
    assert LEVEL_COLORS["WARNING"] != LEVEL_COLORS["ERROR"]


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


def test_log_record_format_hides_source_and_uses_proportional_columns():
    handler = BoundedLogHandler()
    logger = logging.Logger("LabApp.internal.source")  # noqa: LOG001
    logger.addHandler(handler)
    logger.warning("a message\nwith a second line")
    entry = handler.snapshot()[0][0]

    assert entry.logger_name == "LabApp.internal.source"
    assert entry.formatted_text.startswith(f"{entry.timestamp}\tWARNING\ta message")
    assert "\t\twith a second line" in entry.formatted_text
    assert "LabApp.internal.source" not in entry.formatted_text
    assert " | " not in entry.formatted_text


def test_concurrent_burst_keeps_only_recent_bounded_records():
    logger = logging.Logger("LabApp.test.burst")  # noqa: LOG001
    handler = BoundedLogHandler(capacity=2500)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    def emit_range(start: int) -> None:
        for index in range(1500):
            logger.log(
                (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)[index % 5],
                "record %d",
                start + index,
            )

    workers = [threading.Thread(target=emit_range, args=(worker * 1500,)) for worker in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    entries, total = handler.snapshot()
    assert total == 6000
    assert len(entries) == 2500
    assert entries[0].sequence == 3501
    assert entries[-1].sequence == 6000
    assert {entry.levelname for entry in entries} == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


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

    for level, message in (
        (logging.DEBUG, "filter debug"),
        (logging.INFO, "filter info"),
        (logging.WARNING, "filter warning"),
        (logging.ERROR, "filter error"),
        (logging.CRITICAL, "filter critical"),
    ):
        logger.log(level, message)
    qtbot.waitUntil(lambda: "filter critical" in console.text.toPlainText(), timeout=2000)
    for selector, expected, hidden in (
        ("All", ("debug", "info", "warning", "error", "critical"), ()),
        ("INFO+", ("info", "warning", "error", "critical"), ("debug",)),
        ("WARNING+", ("warning", "error", "critical"), ("debug", "info")),
        ("ERROR+", ("error", "critical"), ("debug", "info", "warning")),
        ("CRITICAL", ("critical",), ("debug", "info", "warning", "error")),
    ):
        console.level_filter.setCurrentText(selector)
        displayed = console.text.toPlainText().casefold()
        assert all(f"filter {word}" in displayed for word in expected)
        assert all(f"filter {word}" not in displayed for word in hidden)
    console.level_filter.setCurrentText("All")
    assert "filter debug" in console.text.toPlainText()
    assert any(entry.levelno == logging.DEBUG for entry in console._visible_entries)

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


def test_overflow_notice_appears_only_after_viewer_records_are_dropped(qtbot):
    logger = logging.Logger("LabApp.test.overflow")  # noqa: LOG001
    logger.setLevel(logging.DEBUG)
    console = LogConsole(logger)
    qtbot.addWidget(console)
    assert console.overflow_notice.isHidden()

    for index in range(5001):
        logger.log(
            (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)[index % 5], "item %d", index
        )
    console._drain()

    assert len(console._visible_entries) == 5000
    assert not console.overflow_notice.isHidden()
    assert "older records were dropped (1)" in console.overflow_notice.text()
    assert "file logging is unaffected" in console.overflow_notice.text()


def test_copy_visible_uses_qt_clipboard(qtbot):
    console = LogConsole(logging.Logger("LabApp.test.copy"))  # noqa: LOG001
    qtbot.addWidget(console)
    console.text.setPlainText("first visible line\nsecond visible line")
    console.copy_visible()
    assert QApplication.clipboard().text() == "first visible line\nsecond visible line"
    cursor = console.text.textCursor()
    cursor.movePosition(cursor.MoveOperation.Start)
    cursor.select(cursor.SelectionType.LineUnderCursor)
    console.text.setTextCursor(cursor)
    qtbot.keyClick(console.text, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "first visible line"


def test_console_stops_gui_polling_during_teardown(qtbot):
    console = LogConsole(logging.Logger("LabApp.test.teardown"))  # noqa: LOG001
    qtbot.addWidget(console)
    assert console._timer.isActive()
    console.stop_updates()
    assert not console._timer.isActive()


def test_compact_toolbar_icons_have_accessible_actions(qtbot):
    console = LogConsole(logging.Logger("LabApp.test.icons"))  # noqa: LOG001
    qtbot.addWidget(console)
    console.show()
    buttons = (console.pause_button, console.clear_button, console.copy_button, console.open_button)
    assert all(not button.icon().isNull() for button in buttons)
    assert [button.iconSize().width() for button in buttons] == [18, 18, 18, 18]
    assert [button.accessibleName() for button in buttons] == [
        "Pause logging display",
        "Clear displayed logs (file unaffected)",
        "Copy visible logs",
        "Open application log file",
    ]
    assert console.run_diagnostics_button.text() == "Run Diagnostics"
    assert console.text.toPlainText() == ""
    assert not hasattr(console, "status")
    assert console.overflow_notice.isHidden()
    console.tabs.setCurrentWidget(console.diagnostics_page)
    assert console.refresh_diagnostics_button.isVisible()
    assert console.copy_diagnostics_button.isVisible()
    assert not console.pause_button.isVisible()
    assert not console.search.isVisible()


def test_console_uses_regular_geist_and_font_aware_tab_columns(qtbot):
    from ui.typography import install_application_fonts, make_font

    install_application_fonts(QApplication.instance())
    console = LogConsole(logging.Logger("LabApp.test.typography"))  # noqa: LOG001
    qtbot.addWidget(console)

    assert console.text.font().family() == make_font("sans", 10).family()
    assert console.text.font().family() != make_font("mono", 10).family()
    tabs = console.text.document().defaultTextOption().tabArray()
    assert len(tabs) == 2
    assert tabs[0] < tabs[1]


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
    assert "MISSING_OPTIONAL" in console.diagnostics_text.toPlainText()
    assert "NOT_CHECKED" in console.diagnostics_text.toPlainText()
    qtbot.waitUntil(lambda: "diagnostics completed" in console.text.toPlainText(), timeout=2000)
    assert "MISSING_OPTIONAL" not in console.text.toPlainText()
    console.copy_diagnostics()
    assert "MISSING_OPTIONAL" in QApplication.clipboard().text()


def test_diagnostics_failure_is_visible_and_logged(qtbot, monkeypatch):
    from tools import check_environment

    def fail_collection():
        raise RuntimeError("static inspection failed")

    monkeypatch.setattr(check_environment, "collect_diagnostics", fail_collection)
    logger = logging.Logger("LabApp.test.diagnostics.failure")  # noqa: LOG001
    console = LogConsole(logger)
    qtbot.addWidget(console)
    console.run_diagnostics()
    assert "static inspection failed" in console.diagnostics_text.toPlainText()
    assert "Environment diagnostics failed" in console.handler.snapshot()[0][-1].formatted_text
