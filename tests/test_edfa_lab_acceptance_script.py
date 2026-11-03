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


def test_script_disables_physical_parameter_writes_in_copied_config() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Copy-Item -LiteralPath $LabConfig -Destination $readonlyIni" in source
    assert 'cfg[instrument]["edfa_m4_write_enabled"] = "false"' in source
    assert '"GPIB0::12::INSTR"' in source
    assert "GuiApproved" not in source
    assert "GUI-WRITE-APPROVED" not in source


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
