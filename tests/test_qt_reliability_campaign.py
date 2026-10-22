"""A green launcher must not mask a native pytest failure in the manifest."""

import json
import sys
from pathlib import Path

from tools.qt_reliability_campaign import run_campaign


def test_campaign_fails_closed_without_retry_after_green_summary(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    folder = repository / "tools"
    folder.mkdir(parents=True)
    (folder / "ci_pytest_partition.py").write_text("print('collection parity verified')\n", encoding="utf-8")
    (folder / "qt_stall_stress_supervisor.py").write_text(
        "import argparse,json\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('--output');a,extra=p.parse_known_args()\n"
        "out=Path(a.output);out.mkdir()\n"
        "(out/'manifest.json').write_text(json.dumps({'returncode':3221226505,"
        "'timed_out':False,'last_progress':'193 passed'}))\n"
        "print('193 passed')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.qt_reliability_campaign.subprocess.check_output", lambda *a, **k: "test-revision\n")
    output = tmp_path / "campaign"
    assert run_campaign(repository, output, Path(sys.executable), {"camera": 3, "plot": 2}, 0) == 1
    report = json.loads((output / "campaign.json").read_text(encoding="utf-8"))
    assert report["complete"] is False
    assert len(report["runs"]) == 1
    assert report["runs"][0]["supervisor_returncode"] == 0
    assert report["runs"][0]["child_returncode"] == 3221226505
    assert report["runs"][0]["passed"] is False
