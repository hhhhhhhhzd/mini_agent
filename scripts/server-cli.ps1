[CmdletBinding()]
param(
    [ValidateSet("standard", "trusted", "locked")]
    [string]$PermissionMode = "trusted",
    [switch]$DisableShell,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$agentRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$workspace = Join-Path $agentRoot "workspace"
$dataDir = Join-Path $agentRoot "data"

& (Join-Path $PSScriptRoot "start-cli.ps1") `
    -AgentRoot $agentRoot `
    -Workspace $workspace `
    -DataDir $dataDir `
    -PermissionMode $PermissionMode `
    -DisableShell:$DisableShell `
    -PromptForApiKey `
    -ValidateOnly:$ValidateOnly
