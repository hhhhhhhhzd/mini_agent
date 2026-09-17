[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [string]$AgentRoot,

    [string]$DataDir = (Join-Path $env:LOCALAPPDATA "MiniAgent"),

    [string]$WeixinAcpVersion = "0.6.0",

    [switch]$Login,

    [switch]$DisableShell,

    [string]$ConfigPath,

    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

if (-not $AgentRoot) {
    $AgentRoot = Split-Path -Parent $PSScriptRoot
}

$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$agentRootPath = (Resolve-Path -LiteralPath $AgentRoot).Path
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $agentRootPath "config.json"
}
$configFilePath = (Resolve-Path -LiteralPath $ConfigPath).Path
$agentExecutable = Join-Path $agentRootPath "tmp\venv\Scripts\mini-agent-acp.exe"

if (-not (Test-Path -LiteralPath $agentExecutable -PathType Leaf)) {
    throw "ACP Agent executable not found: $agentExecutable"
}

$nodeCommand = Get-Command node -ErrorAction Stop
$npxCommand = Get-Command npx.cmd, npx -ErrorAction Stop | Select-Object -First 1
$nodeVersionText = (& $nodeCommand.Source --version).Trim().TrimStart("v")
$nodeMajor = [int]($nodeVersionText.Split(".")[0])
if ($nodeMajor -lt 22) {
    throw "weixin-acp requires Node.js 22 or newer; found $nodeVersionText"
}

$dataDirPath = [IO.Path]::GetFullPath($DataDir)
$npmCache = Join-Path $agentRootPath "tmp\npm-cache"

Write-Output "Workspace: $workspacePath"
Write-Output "Agent: $agentExecutable"
Write-Output "Data: $dataDirPath"
Write-Output "Node.js: $nodeVersionText"
Write-Output "weixin-acp: $WeixinAcpVersion"
Write-Output "Permission mode: trusted"
Write-Output ("PowerShell tool: " + $(if ($DisableShell) { "disabled" } else { "enabled" }))

if ($ValidateOnly) {
    Write-Output "Validation completed; no package was downloaded and no account login was attempted."
    return
}

$previousDataDir = [Environment]::GetEnvironmentVariable("MINI_AGENT_DATA_DIR", "Process")
$previousPermissionMode = [Environment]::GetEnvironmentVariable("MINI_AGENT_PERMISSION_MODE", "Process")
$previousShell = [Environment]::GetEnvironmentVariable("MINI_AGENT_ENABLE_SHELL", "Process")
$previousNpmCache = [Environment]::GetEnvironmentVariable("npm_config_cache", "Process")
$previousConfig = [Environment]::GetEnvironmentVariable("MINI_AGENT_CONFIG", "Process")
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
    $env:MINI_AGENT_CONFIG = $configFilePath
    New-Item -ItemType Directory -Path $dataDirPath -Force | Out-Null
    New-Item -ItemType Directory -Path $npmCache -Force | Out-Null

    $env:MINI_AGENT_DATA_DIR = $dataDirPath
    $env:MINI_AGENT_PERMISSION_MODE = "trusted"
    $env:MINI_AGENT_ENABLE_SHELL = $(if ($DisableShell) { "0" } else { "1" })
    $env:npm_config_cache = $npmCache

    Push-Location $workspacePath
    $locationPushed = $true
    if ($Login) {
        & $npxCommand.Source --yes "weixin-acp@$WeixinAcpVersion" login
        if ($LASTEXITCODE -ne 0) {
            throw "weixin-acp login failed with exit code $LASTEXITCODE"
        }
    }

    & $npxCommand.Source --yes "weixin-acp@$WeixinAcpVersion" start -- $agentExecutable
    if ($LASTEXITCODE -ne 0) {
        throw "weixin-acp exited with code $LASTEXITCODE"
    }
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    Restore-ProcessEnvironment "MINI_AGENT_CONFIG" $previousConfig
    Restore-ProcessEnvironment "MINI_AGENT_DATA_DIR" $previousDataDir
    Restore-ProcessEnvironment "MINI_AGENT_PERMISSION_MODE" $previousPermissionMode
    Restore-ProcessEnvironment "MINI_AGENT_ENABLE_SHELL" $previousShell
    Restore-ProcessEnvironment "npm_config_cache" $previousNpmCache
}
