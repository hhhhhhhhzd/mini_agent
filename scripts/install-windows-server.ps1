[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\Users\Administrator\Desktop\agent\MiniAgent",
    [string]$PythonVersion = "3.10.11",
    [int]$NodeMajor = 22,
    [switch]$SkipNode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Native(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Assert-OfficialPythonInstaller([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Python installer signature is not valid: $($signature.Status)"
    }
    if ($signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "Unexpected Python installer signer: $($signature.SignerCertificate.Subject)"
    }
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Mini Agent server deployment requires 64-bit Windows."
}

$scriptAgentRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$requestedRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd("\")
if ($scriptAgentRoot.TrimEnd("\") -ine $requestedRoot) {
    throw "Clone Mini Agent to $requestedRoot before running this script. Current repository: $scriptAgentRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $scriptAgentRoot "pyproject.toml") -PathType Leaf)) {
    throw "pyproject.toml was not found under $scriptAgentRoot"
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$runtimeRoot = Join-Path $scriptAgentRoot "runtime"
$downloadRoot = Join-Path $runtimeRoot "downloads"
$pythonHome = Join-Path $runtimeRoot "python"
$nodeHome = Join-Path $runtimeRoot "node"
$venvRoot = Join-Path $scriptAgentRoot "tmp\venv"
$dataRoot = Join-Path $scriptAgentRoot "data"
$workspaceRoot = Join-Path $scriptAgentRoot "workspace"

foreach ($directory in @($runtimeRoot, $downloadRoot, $dataRoot, $workspaceRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$runningAgent = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venvRoot, [StringComparison]::OrdinalIgnoreCase)
}
if ($runningAgent) {
    $ids = ($runningAgent | Select-Object -ExpandProperty ProcessId) -join ", "
    throw "Stop the running Mini Agent processes before installation or upgrade. PIDs: $ids"
}

Write-Step "Installing private Python $PythonVersion runtime"
$pythonExe = Join-Path $pythonHome "python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    $pythonInstallerName = "python-$PythonVersion-amd64.exe"
    $pythonInstaller = Join-Path $downloadRoot $pythonInstallerName
    $pythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$pythonInstallerName"
    if (-not (Test-Path -LiteralPath $pythonInstaller -PathType Leaf)) {
        Write-Host "Downloading $pythonUrl"
        Invoke-WebRequest -UseBasicParsing -Uri $pythonUrl -OutFile $pythonInstaller
    }
    Assert-OfficialPythonInstaller $pythonInstaller
    $installArguments = @(
        "/quiet",
        "InstallAllUsers=0",
        "Include_launcher=0",
        "Include_test=0",
        "Include_doc=0",
        "Include_tcltk=0",
        "Include_pip=1",
        "PrependPath=0",
        "Shortcuts=0",
        "TargetDir=$pythonHome"
    )
    $process = Start-Process -FilePath $pythonInstaller -ArgumentList $installArguments -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Python installer exited with code $($process.ExitCode)"
    }
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python installation did not create $pythonExe"
}
$installedPython = (& $pythonExe -c "import platform; print(platform.python_version())").Trim()
if (-not $installedPython.StartsWith("$PythonVersion")) {
    throw "Expected Python $PythonVersion, found $installedPython"
}
Write-Host "Python: $installedPython ($pythonExe)"

if (-not $SkipNode) {
    Write-Step "Installing private Node.js $NodeMajor LTS runtime"
    $nodeExe = Join-Path $nodeHome "node.exe"
    if (-not (Test-Path -LiteralPath $nodeExe -PathType Leaf)) {
        $releases = Invoke-RestMethod -UseBasicParsing -Uri "https://nodejs.org/dist/index.json"
        $release = $releases | Where-Object {
            $_.version -match "^v$NodeMajor\." -and
            $_.lts -and
            $_.files -contains "win-x64-zip"
        } | Select-Object -First 1
        if (-not $release) {
            throw "No Node.js $NodeMajor LTS win-x64 ZIP release was found."
        }
        $nodeVersion = [string]$release.version
        $nodeArchiveName = "node-$nodeVersion-win-x64.zip"
        $nodeArchive = Join-Path $downloadRoot $nodeArchiveName
        $nodeBaseUrl = "https://nodejs.org/dist/$nodeVersion"
        if (-not (Test-Path -LiteralPath $nodeArchive -PathType Leaf)) {
            Write-Host "Downloading $nodeBaseUrl/$nodeArchiveName"
            Invoke-WebRequest -UseBasicParsing -Uri "$nodeBaseUrl/$nodeArchiveName" -OutFile $nodeArchive
        }
        $checksums = (Invoke-WebRequest -UseBasicParsing -Uri "$nodeBaseUrl/SHASUMS256.txt").Content
        $escapedName = [Regex]::Escape($nodeArchiveName)
        $checksumMatch = [Regex]::Match(
            $checksums,
            "(?m)^([0-9a-fA-F]{64})\s+$escapedName\s*$"
        )
        if (-not $checksumMatch.Success) {
            throw "Checksum for $nodeArchiveName was not found in SHASUMS256.txt"
        }
        $expectedHash = $checksumMatch.Groups[1].Value.ToUpperInvariant()
        $actualHash = (Get-FileHash -LiteralPath $nodeArchive -Algorithm SHA256).Hash
        if ($actualHash -ne $expectedHash) {
            throw "Node.js archive checksum mismatch. Expected $expectedHash, got $actualHash"
        }
        $extractRoot = Join-Path $runtimeRoot "node-extract"
        if (Test-Path -LiteralPath $extractRoot) {
            Remove-Item -LiteralPath $extractRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
        Expand-Archive -LiteralPath $nodeArchive -DestinationPath $extractRoot -Force
        $expandedNode = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
        if (-not $expandedNode -or -not (Test-Path -LiteralPath (Join-Path $expandedNode.FullName "node.exe"))) {
            throw "The Node.js archive did not contain the expected runtime."
        }
        if (Test-Path -LiteralPath $nodeHome) {
            Remove-Item -LiteralPath $nodeHome -Recurse -Force
        }
        Move-Item -LiteralPath $expandedNode.FullName -Destination $nodeHome
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
    $installedNode = (& $nodeExe --version).Trim()
    if ($installedNode -notmatch "^v$NodeMajor\.") {
        throw "Expected Node.js $NodeMajor, found $installedNode"
    }
    $env:PATH = "$nodeHome;$env:PATH"
    Write-Host "Node.js: $installedNode ($nodeExe)"
}

Write-Step "Creating Mini Agent virtual environment"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-Native $pythonExe -m venv $venvRoot
}
Invoke-Native $venvPython -m pip install --upgrade pip
Invoke-Native $venvPython -m pip install --upgrade $scriptAgentRoot
Invoke-Native $venvPython -m pip check

$agentVersion = (& $venvPython -c "import mini_agent; print(mini_agent.__version__)").Trim()
if ($agentVersion -ne "0.2.0") {
    throw "Expected Mini Agent 0.2.0, found $agentVersion"
}
Write-Host "Mini Agent: $agentVersion"

Write-Step "Validating server launchers"
$cliLauncher = Join-Path $PSScriptRoot "server-cli.ps1"
$weixinLauncher = Join-Path $PSScriptRoot "server-weixin.ps1"
& $cliLauncher -ValidateOnly
if (-not $SkipNode) {
    & $weixinLauncher -ValidateOnly
}

$installInfo = [ordered]@{
    installed_at = [DateTimeOffset]::Now.ToString("o")
    install_root = $scriptAgentRoot
    mini_agent = $agentVersion
    python = $installedPython
    node = $(if ($SkipNode) { $null } else { $installedNode })
    data_dir = $dataRoot
    workspace = $workspaceRoot
}
$installInfo | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot "install-info.json") -Encoding UTF8

Write-Step "Installation completed"
Write-Host "CLI:    powershell -ExecutionPolicy Bypass -File `"$cliLauncher`""
if (-not $SkipNode) {
    Write-Host "WeChat: powershell -ExecutionPolicy Bypass -File `"$weixinLauncher`" -Login"
}
Write-Host "Data:   $dataRoot"
Write-Host "Work:   $workspaceRoot"
