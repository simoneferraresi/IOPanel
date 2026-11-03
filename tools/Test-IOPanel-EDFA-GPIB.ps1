#requires -Version 5.1
<#
.SYNOPSIS
  Offline or strictly read-only M4-A EDFA GPIB acceptance; manual GUI outcome required.
.DESCRIPTION
  Pin an independently reviewed Git SHA from the diagnostic branch, preserve a
  separate detached worktree, never edit the original laboratory INI, and
  never issue EDFA setpoint, emission, reset, IFC or recovery commands.
.EXAMPLE
  .\Test-IOPanel-EDFA-GPIB.ps1 -ExpectedSha <40-character-commit-SHA> -Stage Offline
.EXAMPLE
  .\Test-IOPanel-EDFA-GPIB.ps1 -ExpectedSha <40-character-commit-SHA> -Stage ReadOnly
#>
[CmdletBinding()]
param(
    [ValidateSet('Offline','ReadOnly','GuiReadOnly')]
    [string]$Stage = 'Offline',
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedSha,
    [string]$RepoPath = 'C:\Users\bellanca\Desktop\IOPanel',
    [string]$WorktreePath = 'C:\Users\bellanca\Desktop\IOPanel-GPIB-diagnostics',
    [string]$LabConfig = '',
    [ValidateRange(1,3)]
    [int]$Sessions = 1,
    [ValidateSet(0,10,50,100)]
    [int]$InterqueryDelayMs = 0
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$branch = 'fix/edfa-gpib-forensic-diagnostics'
$root = Join-Path $env:LOCALAPPDATA 'IOPanel\EDFA-GPIB-qualification'
$runDir = Join-Path $root (Get-Date -Format 'yyyyMMdd-HHmmss-ffff')
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$transcript = Join-Path $runDir 'PowerShell-transcript.txt'
$summaryPath = Join-Path $runDir 'acceptance.json'
$guiLog = Join-Path $runDir 'IOPanel-GUI.log'
$report = [ordered]@{
    stage = $Stage
    status = 'FAILED'
    branch = $branch
    tested_commit = $ExpectedSha
    started_utc = (Get-Date).ToUniversalTime().ToString('o')
    offline = 'NOT_RUN'
    read_only = 'NOT_RUN'
    gui = 'NOT_RUN'
    physical_writes = 'DISABLED'
    optical_emission_state = 'NOT VERIFIED BY SOFTWARE'
    result_directory = $runDir
    failure = $null
}
function Assert-Native([string]$label) {
    if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" }
}
function Require-Gate([string]$prompt, [string]$expected) {
    $given = Read-Host $prompt
    if ($given -cne $expected) { throw "Operator gate declined: $prompt" }
}
function Run-Captured([string]$label, [string]$filename, [string[]]$arguments) {
    $path = Join-Path $runDir $filename
    # Windows PowerShell 5.1 may treat a native program's informational stderr
    # as NativeCommandError when redirected while ErrorActionPreference is Stop.
    # Localize the compatibility workaround to the native call, then determine
    # actual process success strictly from LASTEXITCODE (never from stderr).
    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & uv @arguments *> $path
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($code -ne 0) {
        Write-Host "--- $label log tail ---"
        Get-Content -LiteralPath $path -Tail 60 | Out-Host
        throw "$label failed (exit $code); see $path"
    }
}
Start-Transcript -Path $transcript | Out-Null
try {
    Get-Command git -ErrorAction Stop | Out-Null
    Get-Command uv -ErrorAction Stop | Out-Null
    if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) {
        throw "Repository not found: $RepoPath"
    }
    if (-not $LabConfig) { $LabConfig = Join-Path $RepoPath 'config.ini' }
    if (-not (Test-Path -LiteralPath $LabConfig -PathType Leaf)) {
        throw "Reviewed laboratory INI not found: $LabConfig"
    }
    $report.lab_config_sha256 = (Get-FileHash -LiteralPath $LabConfig -Algorithm SHA256).Hash

    & git -C $RepoPath fetch --no-tags origin ("refs/heads/" + $branch + ":refs/remotes/origin/" + $branch)
    Assert-Native 'Fetch diagnostic branch'
    $head = (& git -C $RepoPath rev-parse ("refs/remotes/origin/" + $branch)).Trim()
    Assert-Native 'Resolve pinned branch'
    if ($head -cne $ExpectedSha) {
        throw "Refusing changed/unaudited branch head $head (expected $ExpectedSha)"
    }
    if (-not (Test-Path -LiteralPath $WorktreePath)) {
        & git -C $RepoPath worktree add --detach $WorktreePath $ExpectedSha
        Assert-Native 'Create isolated worktree'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $WorktreePath '.git'))) {
        throw "Existing worktree path does not contain a Git metadata link"
    }
    $checkout = (& git -C $WorktreePath rev-parse HEAD).Trim()
    Assert-Native 'Resolve isolated worktree'
    if ($checkout -cne $ExpectedSha) {
        throw "Existing worktree at unexpected commit $checkout; refusing to modify it"
    }
    & git -C $WorktreePath diff --quiet
    Assert-Native 'Verify clean worktree'
    & git -C $WorktreePath diff --cached --quiet
    Assert-Native 'Verify clean index'
    Write-Host "Verified isolated SHA: $checkout"
    Push-Location $WorktreePath
    try {
        Run-Captured 'uv sync offline' 'uv-sync.log' @('sync','--locked','--no-default-groups','--extra','test','--extra','lint','--extra','typecheck')
        Run-Captured 'Ruff check' 'ruff-check.log' @('run','--no-sync','ruff','check','.')
        Run-Captured 'Ruff format' 'ruff-format.log' @('run','--no-sync','ruff','format','--check','.')
        Run-Captured 'Pyright' 'pyright.log' @('run','--no-sync','pyright')
        $oldQt = $env:QT_QPA_PLATFORM
        $env:QT_QPA_PLATFORM = 'offscreen'
        try {
            Run-Captured 'EDFA fake VISA tests' 'edfa-tests.log' @(
                'run','--no-sync','python','-m','pytest','-q',
                'tests/test_edfa_gpib_diagnostics.py',
                'tests/test_edfa_gpib_transport.py',
                'tests/test_edfa_extended_readback.py',
                'tests/test_edfa_supervised_controls.py'
            )
        } finally {
            $env:QT_QPA_PLATFORM = $oldQt
        }
        $report.offline = 'PASS'
        Write-Host 'Offline checks PASS. No VISA session was opened.'

        $readonlyIni = Join-Path $runDir 'config.gpib.readonly.ini'
        Copy-Item -LiteralPath $LabConfig -Destination $readonlyIni
        $sanitize = @'
import configparser, pathlib, sys
path, logfile = map(pathlib.Path, sys.argv[1:3])
cfg = configparser.ConfigParser(interpolation=None)
with path.open(encoding="utf-8-sig") as src:
    cfg.read_file(src)
instrument = next((s for s in cfg.sections() if s.casefold() == "instruments"), None)
if instrument is None:
    raise SystemExit("Missing [Instruments]")
cfg[instrument]["edfa_m4_write_enabled"] = "false"
logging_section = next((s for s in cfg.sections() if s.casefold() == "logging"), None)
if logging_section is None:
    cfg.add_section("Logging")
    logging_section = "Logging"
cfg[logging_section]["file"] = str(logfile)
cfg[logging_section]["level"] = "DEBUG"
with path.open("w", encoding="utf-8") as dst:
    cfg.write(dst)
from tools.edfa_readonly_probe import read_configuration
profile = read_configuration(path).instruments
if profile.edfa_backend != "physical" or profile.edfa_gpib_resource_name != "GPIB0::12::INSTR":
    raise SystemExit("Unexpected EDFA backend/address; stop")
if profile.edfa_m4_write_enabled:
    raise SystemExit("Physical EDFA parameter writes are still enabled; stop")
'@
        & uv run --no-sync python -c $sanitize $readonlyIni $guiLog
        Assert-Native 'Create and validate read-only INI'
        Run-Captured 'No-VISA dry run' 'dry-run.log' @(
            'run','--no-sync','python','-m','tools.edfa_gpib_diagnostics','--config',$readonlyIni
        )
        if ($Stage -eq 'Offline') {
            $report.status = 'PASS_OFFLINE_ONLY'
        } else {
            Write-Host 'STOP GATE: independently verify emission OFF, optical route and interlocks.'
            Write-Host 'Confirm the EDFA GPIB address, NI controller, exclusive bus owner and approval.'
            Write-Host 'Even query-only connections can affect GPIB remote/local state.'
            Require-Gate 'Type READONLY-APPROVED to run bounded physical queries' 'READONLY-APPROVED'
            $maxQueries = $Sessions * 16
            Run-Captured 'Bounded physical read-only diagnostic' 'edfa-physical-probe.log' @(
                'run','--no-sync','python','-m','tools.edfa_gpib_diagnostics',
                '--config',$readonlyIni,'--connect-readonly','--operator-confirmed',
                '--plan','telemetry-configuration','--sessions',"$Sessions",
                '--max-queries',"$maxQueries",'--max-seconds','120',
                '--interquery-delay-ms',"$InterqueryDelayMs",'--report-dir',$runDir
            )
            $report.read_only = 'PASS_READ_ONLY_ONLY'
            $report.status = 'PASS_READ_ONLY_ONLY'
            Write-Host 'Read-only query plan completed; this is NOT authorization for EDFA writes.'
            if ($Stage -eq 'GuiReadOnly') {
                Write-Host 'GUI may initialize CT400, VmbPy cameras, piezo discovery and MATLAB.'
                Write-Host 'No GUI Apply, setter, reset or emission controls may be used.'
                Require-Gate 'Type LAUNCH-GUI to launch other laboratory components' 'LAUNCH-GUI'
                Run-Captured 'Install optional GUI dependencies' 'uv-sync-gui.log' @(
                    'sync','--locked','--extra','test','--extra','lint','--extra','typecheck'
                )
                Run-Captured 'GUI process' 'gui-stdout.log' @(
                    'run','--no-sync','python','app.py','--config',$readonlyIni
                )
                $contents = ''
                if (Test-Path -LiteralPath $guiLog) {
                    $contents += (Get-Content -LiteralPath $guiLog -Raw)
                } else {
                    throw "GUI application log missing; no GUI acceptance possible"
                }
                $contents += (Get-Content -LiteralPath (Join-Path $runDir 'gui-stdout.log') -Raw)
                if ($contents -match '(?i)VI_ERROR_NLISTENERS|EDFACommunicationError|EDFA[^\r\n]*(failed|exception|error)') {
                    $report.gui = 'FAIL_EDFA_COMMUNICATION'
                    throw "EDFA error detected in GUI logs. GUI qualification FAILED."
                }
                $report.gui = 'MANUAL_CONFIRMATION_PENDING'
                Write-Host 'GUI exited without a detected EDFA log error; this is NOT a hardware pass.'
                Require-Gate 'After visual review of EDFA actions, type GUI-READONLY-PASS' 'GUI-READONLY-PASS'
                $report.gui = 'PASS_MANUALLY_ATTESTED'
                $report.status = 'PASS_GUI_READ_ONLY_ONLY'
            }
        }
    } finally {
        Pop-Location
    }
} catch {
    $report.status = 'FAILED'
    $report.failure = $_.Exception.Message
    Write-Host ("QUALIFICATION FAILED: " + $_.Exception.Message) -ForegroundColor Red
} finally {
    $report.finished_utc = (Get-Date).ToUniversalTime().ToString('o')
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Stop-Transcript | Out-Null
}
Write-Host "Acceptance result: $($report.status)"
Write-Host "Evidence directory: $runDir"
if ($report.status -eq 'FAILED') { exit 2 }
exit 0
