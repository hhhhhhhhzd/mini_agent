[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [string]$AgentRoot,

    [string]$DataDir = (Join-Path $env:LOCALAPPDATA "MiniAgent"),

    [ValidateSet("standard", "trusted", "locked")]
    [string]$PermissionMode = "trusted",

    [switch]$DisableShell,

    [switch]$PromptForApiKey,

    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

if (-not $AgentRoot) {
    $AgentRoot = Split-Path -Parent $PSScriptRoot
}

$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$agentRootPath = (Resolve-Path -LiteralPath $AgentRoot).Path
$dataDirPath = [IO.Path]::GetFullPath($DataDir)
$agentExecutable = Join-Path $agentRootPath "tmp\venv\Scripts\mini-agent.exe"

if (-not (Test-Path -LiteralPath $agentExecutable -PathType Leaf)) {
    throw "CLI Agent executable not found: $agentExecutable"
}

Write-Output "Workspace: $workspacePath"
Write-Output "Agent: $agentExecutable"
Write-Output "Data: $dataDirPath"
Write-Output "Permission mode: $PermissionMode"
Write-Output ("PowerShell tool: " + $(if ($DisableShell) { "disabled" } else { "enabled" }))

if ($ValidateOnly) {
    Write-Output "Validation completed; the Agent was not started and no API request was sent."
    return
}

$promptedApiKey = $false
if (-not $env:MINI_AGENT_API_KEY -and -not $env:DASHSCOPE_API_KEY) {
    if (-not $PromptForApiKey) {
        throw "Set MINI_AGENT_API_KEY (or DASHSCOPE_API_KEY), or pass -PromptForApiKey."
    }
    $secureApiKey = Read-Host "DashScope API Key" -AsSecureString
    $credential = [PSCredential]::new("MiniAgent", $secureApiKey)
    $plainApiKey = $credential.GetNetworkCredential().Password
    if (-not $plainApiKey) {
        throw "API Key cannot be empty."
    }
    $env:MINI_AGENT_API_KEY = $plainApiKey
    $promptedApiKey = $true
}

$previousDataDir = [Environment]::GetEnvironmentVariable("MINI_AGENT_DATA_DIR", "Process")
$previousPermissionMode = [Environment]::GetEnvironmentVariable("MINI_AGENT_PERMISSION_MODE", "Process")
$previousShell = [Environment]::GetEnvironmentVariable("MINI_AGENT_ENABLE_SHELL", "Process")
$locationPushed = $false

function Restore-ProcessEnvironment(
    [string]$Name,
    [AllowNull()][string]$Value
) {
    if ($null -eq $Value) {
        Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item "Env:$Name" $Value
    }
}

try {
    New-Item -ItemType Directory -Path $dataDirPath -Force | Out-Null
    $env:MINI_AGENT_DATA_DIR = $dataDirPath
    $env:MINI_AGENT_PERMISSION_MODE = $PermissionMode
    $env:MINI_AGENT_ENABLE_SHELL = $(if ($DisableShell) { "0" } else { "1" })

    Push-Location $workspacePath
    $locationPushed = $true
    & $agentExecutable --workspace $workspacePath --data-dir $dataDirPath chat
    if ($LASTEXITCODE -ne 0) {
        throw "CLI Agent exited with code $LASTEXITCODE"
    }
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    Restore-ProcessEnvironment "MINI_AGENT_DATA_DIR" $previousDataDir
    Restore-ProcessEnvironment "MINI_AGENT_PERMISSION_MODE" $previousPermissionMode
    Restore-ProcessEnvironment "MINI_AGENT_ENABLE_SHELL" $previousShell
    if ($promptedApiKey) {
        Remove-Item Env:MINI_AGENT_API_KEY -ErrorAction SilentlyContinue
        $plainApiKey = $null
        $secureApiKey = $null
        $credential = $null
    }
}
