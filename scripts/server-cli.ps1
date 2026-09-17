[CmdletBinding()]
param(
    [string]$Workspace,
    [ValidateSet("standard", "trusted", "locked")]
    [string]$PermissionMode = "trusted",
    [switch]$DisableShell,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$agentRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if (-not $Workspace) {
    $Workspace = Join-Path $agentRoot "workspace"
}
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$dataDir = Join-Path $agentRoot "data"

if (-not $ValidateOnly) {
    $runtimeDir = Join-Path $agentRoot "runtime"
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    [ordered]@{
        mode = "cli"
        workspace = $workspacePath
        disable_shell = [bool]$DisableShell
        permission_mode = $PermissionMode
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir "launch.json") -Encoding UTF8
}

& (Join-Path $PSScriptRoot "start-cli.ps1") `
    -AgentRoot $agentRoot `
    -Workspace $workspacePath `
    -DataDir $dataDir `
    -PermissionMode $PermissionMode `
    -DisableShell:$DisableShell `
    -ValidateOnly:$ValidateOnly
