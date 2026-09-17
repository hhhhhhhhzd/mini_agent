[CmdletBinding()]
param(
    [string]$Workspace,
    [string]$WeixinAcpVersion = "0.6.0",
    [switch]$Login,
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
$nodeHome = Join-Path $agentRoot "runtime\node"
$nodeExe = Join-Path $nodeHome "node.exe"

if (-not (Test-Path -LiteralPath $nodeExe -PathType Leaf)) {
    throw "Private Node.js runtime not found: $nodeExe. Run install-windows-server.ps1 first."
}
$env:PATH = "$nodeHome;$env:PATH"

& (Join-Path $PSScriptRoot "start-weixin-acp.ps1") `
    -AgentRoot $agentRoot `
    -Workspace $workspacePath `
    -DataDir $dataDir `
    -WeixinAcpVersion $WeixinAcpVersion `
    -Login:$Login `
    -DisableShell:$DisableShell `
    -ValidateOnly:$ValidateOnly
