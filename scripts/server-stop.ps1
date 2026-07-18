[CmdletBinding()]
param(
    [ValidateSet("all", "cli", "weixin")]
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"
$agentRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$scriptsRoot = Join-Path $agentRoot "tmp\venv\Scripts"
$nodeRoot = Join-Path $agentRoot "runtime\node"
$cliExecutable = Join-Path $scriptsRoot "mini-agent.exe"
$acpExecutable = Join-Path $scriptsRoot "mini-agent-acp.exe"

$processes = @(Get-CimInstance Win32_Process | Where-Object {
    if (-not $_.ExecutablePath) {
        return $false
    }
    $executable = [IO.Path]::GetFullPath($_.ExecutablePath)
    $isCli = $executable.Equals($cliExecutable, [StringComparison]::OrdinalIgnoreCase)
    $isAcp = $executable.Equals($acpExecutable, [StringComparison]::OrdinalIgnoreCase)
    $isPrivateNode = $executable.StartsWith(
        $nodeRoot + "\",
        [StringComparison]::OrdinalIgnoreCase
    )
    switch ($Mode) {
        "cli" { return $isCli }
        "weixin" { return $isAcp -or $isPrivateNode }
        default { return $isCli -or $isAcp -or $isPrivateNode }
    }
})

if ($processes.Count -eq 0) {
    Write-Output "No running Mini Agent $Mode process was found."
    return
}

$candidateIds = @($processes | ForEach-Object { [int]$_.ProcessId })
$roots = @($processes | Where-Object {
    $candidateIds -notcontains [int]$_.ParentProcessId
})
if ($roots.Count -eq 0) {
    $roots = $processes
}

foreach ($process in $roots) {
    Write-Output "Stopping $($process.Name) PID $($process.ProcessId) and its process tree..."
    & taskkill.exe /PID $process.ProcessId /T /F | Out-Host
    if ($LASTEXITCODE -notin @(0, 128)) {
        throw "taskkill failed for PID $($process.ProcessId) with code $LASTEXITCODE"
    }
}

Write-Output "Mini Agent $Mode processes stopped."
