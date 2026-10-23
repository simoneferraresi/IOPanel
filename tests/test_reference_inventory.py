"""Hardware-free regression tests for the conservative AST inventory tool."""

from __future__ import annotations

import json

from tools.audit_reference_inventory import audit, markdown_report


def test_audit_separates_unreferenced_definitions_from_uses_and_qt_connections(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "example.py").write_text(
        "\n".join(
            [
                "class Dead:",
                "    pass",
                "class Live:",
                "    changed = Signal(int)",
                "    def on_changed(self):",
                "        pass",
                "    def never_called(self):",
                "        pass",
                "    def attach(self):",
                "        self.changed.connect(self.on_changed)",
                "        getattr(self, 'on_changed')",
            ]
        ),
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text("from ui.example import Live\nused = Live\n", encoding="utf-8")

    report = audit(tmp_path)
    candidates = {item["qualified_name"] for item in report["zero_spelling_reference_candidates"]}
    assert report["source_files"] == 2
    assert report["parsed_files"] == 2
    assert report["parse_errors"] == []
    assert "Dead" in candidates
    assert "Live.never_called" in candidates
    assert "Live" not in candidates
    assert "Live.changed" not in candidates
    assert "Live.on_changed" not in candidates
    assert "Live.attach" in candidates
    assert len(report["qt_connection_sites"]) == 1
    assert len(report["dynamic_lookup_sites"]) == 1
    assert {"source": "tests.test_example", "target": "ui.example"} in report["local_import_edges"]
    assert "Dead" in markdown_report(report)
    assert "zero_spelling_reference_candidates" in json.dumps(report)


def test_unparsable_module_is_recorded_without_suppressing_other_results(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "broken.py").write_text("def bad(:\n", encoding="utf-8")
    (ui / "clean.py").write_text("class Unused:\n    pass\n", encoding="utf-8")
    report = audit(tmp_path)
    assert report["parsed_files"] == 1
    assert len(report["parse_errors"]) == 1
    assert report["parse_errors"][0]["path"] == "ui/broken.py"
    assert any(item["name"] == "Unused" for item in report["zero_spelling_reference_candidates"])
