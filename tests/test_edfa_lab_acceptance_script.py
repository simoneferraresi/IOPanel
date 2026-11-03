"""Static safety contract for the operator-gated Windows EDFA acceptance script.

CI cannot execute lab hardware workflows; these tests make unsafe script
changes noticeable without authorizing physical VISA access.
"""

from __future__ import annotations

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "Test-IOPanel-EDFA-GPIB.ps1"


def test_script_requires_pinned_reviewed_sha_and_separate_detached_worktree() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ExpectedSha" in source
    assert "git -C $RepoPath fetch" in source
    assert "worktree add --detach" in source
    assert "if ($head -cne $ExpectedSha)" in source
    assert "if ($checkout -cne $ExpectedSha)" in source


def test_script_prepares_readonly_ini_without_multiline_python_source() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "tools.edfa_prepare_readonly_config" in source
    assert "'--source',$LabConfig" in source
    assert "'--destination',$readonlyIni" in source
    assert "'--log-file',$guiLog" in source
    assert "python -c $sanitize" not in source
    assert "Copy-Item -LiteralPath $LabConfig" not in source
    assert "GuiApproved" not in source
    assert "GUI-WRITE-APPROVED" not in source


def test_offline_status_is_only_marked_pass_after_safe_ini_and_dry_run() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.index("Run-Captured 'Prepare and validate read-only INI'") < source.index(
        "Run-Captured 'No-VISA dry run'"
    )
    assert source.index("Run-Captured 'No-VISA dry run'") < source.index("$report.offline = 'PASS'")
    assert "if ($Stage -eq 'Offline')" in source
    assert "$report.status = 'PASS_OFFLINE_ONLY'" in source
    assert "ls-files --others --exclude-standard" in source


def test_script_distinguishes_gui_process_success_from_manual_acceptance() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "GUI-READONLY-PASS" in source
    assert "MANUAL_CONFIRMATION_PENDING" in source
    assert "FAIL_EDFA_COMMUNICATION" in source
    assert "VI_ERROR_NLISTENERS" in source
    assert "All selected stages completed." not in source


def test_script_requires_operator_approval_for_every_physical_stage() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "READONLY-APPROVED" in source
    assert "LAUNCH-GUI" in source
    assert "--connect-readonly" in source
    assert "--operator-confirmed" in source
    assert "--max-queries" in source
    assert "--max-seconds" in source
    assert "acceptance.json" in source


def test_native_stderr_compatibility_preserves_real_exit_code() -> None:
    """Windows PowerShell 5.1 must not mistake uv progress on stderr for failure."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "$savedErrorActionPreference = $ErrorActionPreference" in source
    assert "$ErrorActionPreference = 'Continue'" in source
    assert "$ErrorActionPreference = $savedErrorActionPreference" in source
    assert "$code = $LASTEXITCODE" in source
    assert "if ($code -ne 0)" in source
    # The same guarded runner handles offline, physical and GUI invocations.
    assert source.count("*>") == 1
    assert "Run-Captured 'Bounded physical read-only diagnostic'" in source
    assert "Run-Captured 'GUI process'" in source
