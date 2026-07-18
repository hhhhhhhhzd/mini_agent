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

& (Join-Path $PSScriptRoot "start-cli.ps1") `
    -AgentRoot $agentRoot `
    -Workspace $workspacePath `
    -DataDir $dataDir `
    -PermissionMode $PermissionMode `
    -DisableShell:$DisableShell `
    -PromptForApiKey `
    -ValidateOnly:$ValidateOnly
