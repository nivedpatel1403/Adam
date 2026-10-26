<#
.SYNOPSIS
    Provisions required telemetry tools inside the ADAM Windows guest VM.
.DESCRIPTION
    Installs Process Monitor (Procmon64.exe) and Wireshark/TShark (tshark.exe),
    accepts Sysinternals EULA, and verifies Sysmon service status.
    Must be executed inside the Windows guest as Administrator.
#>

[CmdletBinding()]
param(
    [string]$ProcmonDir = "C:\Users\Admin\Downloads\ProcessMonitor",
    [string]$WiresharkDir = "C:\Program Files\Wireshark",
    [switch]$SkipSysmonCheck
)

$ErrorActionPreference = "Stop"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "     ADAM Guest Telemetry Tool Provisioning Script     " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# 1. Process Monitor Provisioning
Write-Host "`n[1/3] Checking Process Monitor..." -ForegroundColor Yellow
$procmonExe = Join-Path $ProcmonDir "Procmon64.exe"
if (Test-Path $procmonExe) {
    Write-Host "  [OK] Process Monitor already installed: $procmonExe" -ForegroundColor Green
} else {
    Write-Host "  [-] Procmon64.exe not found. Downloading Sysinternals Process Monitor..." -ForegroundColor Yellow
    if (-not (Test-Path $ProcmonDir)) {
        New-Item -ItemType Directory -Force -Path $ProcmonDir | Out-Null
    }
    $zipPath = Join-Path $env:TEMP "ProcessMonitor.zip"
    $procmonUrl = "https://download.sysinternals.com/files/ProcessMonitor.zip"
    
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $procmonUrl -OutFile $zipPath -UseBasicParsing
        Expand-Archive -Path $zipPath -DestinationPath $ProcmonDir -Force
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        
        # Pre-accept Sysinternals EULA for current user and system
        $regPaths = @(
            "HKCU:\Software\Sysinternals\Process Monitor",
            "HKLM:\SOFTWARE\Sysinternals\Process Monitor"
        )
        foreach ($rp in $regPaths) {
            if (-not (Test-Path $rp)) {
                New-Item -Path $rp -Force | Out-Null
            }
            Set-ItemProperty -Path $rp -Name "EulaAccepted" -Value 1 -Type DWord -Force
        }
        
        if (Test-Path $procmonExe) {
            Write-Host "  [OK] Successfully installed Process Monitor: $procmonExe" -ForegroundColor Green
        } else {
            Write-Host "  [!] Extraction completed but Procmon64.exe not found." -ForegroundColor Red
        }
    } catch {
        Write-Host "  [ERROR] Failed to download or install Process Monitor: $_" -ForegroundColor Red
    }
}

# 2. Wireshark / TShark Provisioning
Write-Host "`n[2/3] Checking TShark (Wireshark CLI)..." -ForegroundColor Yellow
$tsharkExe = Join-Path $WiresharkDir "tshark.exe"
if (Test-Path $tsharkExe) {
    Write-Host "  [OK] TShark already installed: $tsharkExe" -ForegroundColor Green
} else {
    Write-Host "  [-] tshark.exe not found at $tsharkExe" -ForegroundColor Yellow
    Write-Host "  [-] Downloading Wireshark installer..." -ForegroundColor Yellow
    
    $wiresharkUrl = "https://2.na.dl.wireshark.org/win64/Wireshark-win64-latest.exe"
    $installerPath = Join-Path $env:TEMP "wireshark-installer.exe"
    
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $wiresharkUrl -OutFile $installerPath -UseBasicParsing
        Write-Host "  [-] Running Wireshark silent installation (/S)..." -ForegroundColor Yellow
        Start-Process -FilePath $installerPath -ArgumentList "/S", "/desktopicon=no", "/quicklaunchicon=no" -Wait
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        
        if (Test-Path $tsharkExe) {
            Write-Host "  [OK] Successfully installed TShark: $tsharkExe" -ForegroundColor Green
        } else {
            Write-Host "  [!] Wireshark installer finished, verifying path..." -ForegroundColor Yellow
            $found = Get-Command "tshark.exe" -ErrorAction SilentlyContinue
            if ($found) {
                Write-Host "  [OK] TShark found on PATH: $($found.Source)" -ForegroundColor Green
            } else {
                Write-Host "  [!] TShark not found at expected path. Please install Wireshark manually or via Chocolatey: choco install wireshark -y" -ForegroundColor Red
            }
        }
    } catch {
        Write-Host "  [ERROR] Failed to download Wireshark installer: $_" -ForegroundColor Red
        Write-Host "  [TIP] You can install via Chocolatey: 'choco install wireshark -y' or winget: 'winget install WiresharkFoundation.Wireshark'" -ForegroundColor Cyan
    }
}

# 3. Sysmon Service Check
Write-Host "`n[3/3] Checking Microsoft Sysmon service..." -ForegroundColor Yellow
$sysmonService = Get-Service -Name "Sysmon", "Sysmon64" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($sysmonService) {
    Write-Host "  [OK] Sysmon service found: $($sysmonService.Name) (Status: $($sysmonService.Status))" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Sysmon service is not running or not installed." -ForegroundColor Red
    Write-Host "  [INFO] Download Sysmon from https://download.sysinternals.com/files/Sysmon.zip and install with:" -ForegroundColor Cyan
    Write-Host "         .\Sysmon64.exe -i -accepteula -n" -ForegroundColor Cyan
}

# 4. Final Summary
Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "                     Status Summary                    " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Procmon64.exe: $(if (Test-Path $procmonExe) { '[AVAILABLE]' } else { '[MISSING]' })" -ForegroundColor $(if (Test-Path $procmonExe) { 'Green' } else { 'Red' })
Write-Host "  tshark.exe:    $(if (Test-Path $tsharkExe) { '[AVAILABLE]' } else { '[MISSING]' })" -ForegroundColor $(if (Test-Path $tsharkExe) { 'Green' } else { 'Red' })
Write-Host "  Sysmon:        $(if ($sysmonService -and $sysmonService.Status -eq 'Running') { '[RUNNING]' } else { '[NOT RUNNING]' })" -ForegroundColor $(if ($sysmonService -and $sysmonService.Status -eq 'Running') { 'Green' } else { 'Red' })
Write-Host "`nNext Step: If all tools are available, take a VirtualBox snapshot:" -ForegroundColor Cyan
Write-Host "  VBoxManage snapshot \"Windows\" take \"disk-resized-36gb-c-drive\"" -ForegroundColor White
