[CmdletBinding()]
param(
    [ValidateSet("cli", "weixin")][string]$Mode,
    [string]$Workspace,
    [switch]$NoRestart,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$agentRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $agentRoot "tmp\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Run install-windows-server.ps1 first."
}
# Run with the private base runtime: the updater must not lock the venv it restores.
$basePython = & $python -c "import sys; print(sys._base_executable)"
if ($LASTEXITCODE -ne 0) { throw "Cannot locate private Python runtime." }
$updateArgs = @((Join-Path $PSScriptRoot "server_update.py"))
if ($Mode) { $updateArgs += @("--mode", $Mode) }
if ($Workspace) { $updateArgs += @("--workspace", $Workspace) }
if ($NoRestart) { $updateArgs += "--no-restart" }
if ($CheckOnly) { $updateArgs += "--check-only" }
& $basePython @updateArgs
if ($LASTEXITCODE -ne 0) { throw "Update failed. See the error and backup path above." }
