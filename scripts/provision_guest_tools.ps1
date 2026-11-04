#requires -Version 5.1
#requires -RunAsAdministrator
# Install host-staged tools only. No guest Internet access is required.
[CmdletBinding()]
param(
    [string]$ProcmonDir = 'C:\ADAM\Tools\Procmon',
    [string]$ProcmonSourcePath,
    [string]$WiresharkDir = 'C:\Program Files\Wireshark',
    [string]$WiresharkInstallerPath,
    [string]$NpcapInstallerPath,
    [string]$InstallerManifestPath,
    [switch]$NpcapOem,
    [switch]$SkipSysmonCheck
)
$ErrorActionPreference = 'Stop'
function Assert-StagedInstaller([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Host-staged installer is missing: $Path"
    }
    if ($InstallerManifestPath) {
        $manifest = @(Get-Content -LiteralPath $InstallerManifestPath -Raw | ConvertFrom-Json)
        $fileName = [IO.Path]::GetFileName($Path)
        $entry = @($manifest | Where-Object { $_.File -eq $fileName })
        if ($entry.Count -ne 1 -or $entry[0].Status -ne 'Valid' -or
            (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $entry[0].SHA256) {
            throw "Installer does not match the host signature manifest: $fileName"
        }
    } elseif ((Get-AuthenticodeSignature -LiteralPath $Path).Status -ne 'Valid') {
        throw "Installer signature could not be validated: $Path"
    }
}
function Install-Staged([string]$Path, [string[]]$Arguments) {
    Assert-StagedInstaller $Path
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) {
        throw "Installer PID=$($process.Id) is still running; resolve it before retrying"
    }
    if ($process.ExitCode -eq 3010) { throw 'Reboot the guest before verification' }
    if ($process.ExitCode -ne 0) { throw "Installer failed, exit code $($process.ExitCode)" }
}
$procmon = Join-Path $ProcmonDir 'Procmon64.exe'
if (-not (Test-Path -LiteralPath $procmon)) {
    Assert-StagedInstaller $ProcmonSourcePath
    New-Item -ItemType Directory -Path $ProcmonDir -Force | Out-Null
    Copy-Item -LiteralPath $ProcmonSourcePath -Destination $procmon
}
$tshark = Join-Path $WiresharkDir 'tshark.exe'
if (-not (Test-Path -LiteralPath $tshark)) { Install-Staged $WiresharkInstallerPath @('/S') }
if (-not (Test-Path -LiteralPath $tshark)) { throw "Missing tshark after installation: $tshark" }
$npcap = Get-Service -Name npcap -ErrorAction SilentlyContinue
if (-not $npcap) {
    Assert-StagedInstaller $NpcapInstallerPath
    if (-not $NpcapOem) {
        throw "Standard Npcap requires its graphical installer: $NpcapInstallerPath. Complete it, then rerun."
    }
    Install-Staged $NpcapInstallerPath @('/S', '/loopback_support=no')
    $npcap = Get-Service -Name npcap -ErrorAction SilentlyContinue
}
if (-not $npcap) { throw 'Npcap service is absent after installation' }
if ($npcap.Status -ne 'Running') { Start-Service npcap }
$interfaces = & $tshark -D 2>&1
if ($LASTEXITCODE -ne 0 -or -not ($interfaces -match '^\d+\.')) {
    throw "tshark interface enumeration failed: $interfaces"
}
$interfaces | Write-Output
if (-not $SkipSysmonCheck) {
    $sysmon = Get-Service -Name Sysmon,Sysmon64 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $sysmon -or $sysmon.Status -ne 'Running') { throw 'Sysmon is not running' }
}
Write-Output 'Tool checks passed. Real capture and restore verification are still required before promotion.'
