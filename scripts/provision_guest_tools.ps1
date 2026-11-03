<#
.SYNOPSIS
    Provisions required telemetry tools inside the ADAM Windows guest VM.
.DESCRIPTION
    Installs Process Monitor (Procmon64.exe), Npcap capture driver, Wireshark/TShark (tshark.exe),
    accepts Sysinternals EULA, and verifies Sysmon service status.
    Must be executed inside the Windows guest as Administrator.
#>

[CmdletBinding()]
param(
    [string]$ProcmonDir = "C:\ADAM\Tools\Procmon",
    [string]$WiresharkDir = "C:\Program Files\Wireshark",
    [string]$NpcapInstallerPath = $null,
    [switch]$SkipSysmonCheck
)

$ErrorActionPreference = "Stop"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "     ADAM Guest Telemetry Tool Provisioning Script     " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# 1. Process Monitor Provisioning
Write-Host "`n[1/4] Checking Process Monitor..." -ForegroundColor Yellow
$procmonExe = Join-Path $ProcmonDir "Procmon64.exe"
if (Test-Path $procmonExe) {
    Write-Host "  [OK] Process Monitor already installed: $procmonExe" -ForegroundColor Green
} else {
    Write-Host "  [-] Procmon64.exe not found at $procmonExe. Downloading Sysinternals Process Monitor..." -ForegroundColor Yellow
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

# 2. Npcap Packet Capture Driver Provisioning
Write-Host "`n[2/4] Checking Npcap Capture Driver..." -ForegroundColor Yellow
$npcapSvc = Get-Service -Name "npcap" -ErrorAction SilentlyContinue
if ($npcapSvc -and $npcapSvc.Status -eq "Running") {
    Write-Host "  [OK] Npcap service is running." -ForegroundColor Green
} else {
    Write-Host "  [-] Npcap driver not active. Preparing installation..." -ForegroundColor Yellow
    $installerPath = $NpcapInstallerPath
    if (-not $installerPath -or (-not (Test-Path $installerPath))) {
        $installerPath = Join-Path $env:TEMP "npcap-installer.exe"
        $npcapUrl = "https://npcap.com/dist/npcap-1.79.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Write-Host "  [-] Downloading Npcap installer from $npcapUrl..." -ForegroundColor Yellow
            Invoke-WebRequest -Uri $npcapUrl -OutFile $installerPath -UseBasicParsing
        } catch {
            Write-Host "  [WARNING] Direct download of Npcap failed: $_" -ForegroundColor Yellow
        }
    }

    if (Test-Path $installerPath) {
        Write-Host "  [-] Executing automated Npcap installation..." -ForegroundColor Yellow
        try {
            # Try silent flags first (works for OEM or silent-enabled packages)
            $p = Start-Process -FilePath $installerPath -ArgumentList "/S", "/loopback_support=no" -PassThru
            $waited = $p.WaitForExit(15000)
            
            # If still running or GUI wizard appeared (standard non-OEM installer)
            if (-not $waited -or (-not (Get-Service npcap -ErrorAction SilentlyContinue))) {
                Write-Host "  [-] Running UI automation handler for interactive wizard..." -ForegroundColor Yellow
                $wshell = New-Object -ComObject WScript.Shell
                $timeout = [DateTime]::UtcNow.AddSeconds(90)
                while (-not $p.HasExited -and [DateTime]::UtcNow -lt $timeout) {
                    Start-Sleep -Milliseconds 800
                    if ($wshell.AppActivate("Npcap")) {
                        $wshell.SendKeys("%i") # Alt+I for "I Agree" / "Install"
                        Start-Sleep -Milliseconds 500
                        $wshell.SendKeys("%n") # Alt+N for "Next"
                        Start-Sleep -Milliseconds 500
                        $wshell.SendKeys("%f") # Alt+F for "Finish"
                        $wshell.SendKeys("~")  # Enter
                    }
                }
                if (-not $p.HasExited) {
                    $p.WaitForExit(10000) | Out-Null
                }
            }
        } catch {
            Write-Host "  [!] Automated execution failed: $_" -ForegroundColor Yellow
        }
    }

    # Fallback to package managers if available
    $npcapSvc = Get-Service -Name "npcap" -ErrorAction SilentlyContinue
    if (-not $npcapSvc) {
        if (Get-Command choco -ErrorAction SilentlyContinue) {
            Write-Host "  [-] Attempting choco install npcap..." -ForegroundColor Yellow
            choco install npcap -y | Out-Null
        } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "  [-] Attempting winget install Insecure.Npcap..." -ForegroundColor Yellow
            winget install Insecure.Npcap --accept-source-agreements --accept-package-agreements -h | Out-Null
        }
    }

    # Verify and ensure running
    $npcapSvc = Get-Service -Name "npcap" -ErrorAction SilentlyContinue
    if ($npcapSvc) {
        if ($npcapSvc.Status -ne "Running") {
            try { Start-Service npcap -ErrorAction SilentlyContinue } catch { }
        }
        Write-Host "  [OK] Npcap installed and service status: $($npcapSvc.Status)" -ForegroundColor Green
    } else {
        Write-Host "  [WARNING] Npcap driver not yet detected. Tshark packet capture requires Npcap." -ForegroundColor Red
    }
}

# 3. Wireshark / TShark Provisioning
Write-Host "`n[3/4] Checking TShark (Wireshark CLI)..." -ForegroundColor Yellow
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
                $tsharkExe = $found.Source
            } else {
                Write-Host "  [!] TShark not found at expected path. Please install Wireshark manually or via Chocolatey: choco install wireshark -y" -ForegroundColor Red
            }
        }
    } catch {
        Write-Host "  [ERROR] Failed to download Wireshark installer: $_" -ForegroundColor Red
        Write-Host "  [TIP] You can install via Chocolatey: 'choco install wireshark -y' or winget: 'winget install WiresharkFoundation.Wireshark'" -ForegroundColor Cyan
    }
}

# Verify tshark interface detection
if (Test-Path $tsharkExe) {
    Write-Host "  [-] Enumerating network capture interfaces via tshark -D..." -ForegroundColor Yellow
    try {
        $ifaces = & $tsharkExe -D
        foreach ($iface in $ifaces) {
            Write-Host "      $iface" -ForegroundColor Gray
        }
    } catch {
        Write-Host "  [!] Failed to execute tshark -D: $_" -ForegroundColor Yellow
    }
}

# 4. Sysmon Service Check
Write-Host "`n[4/4] Checking Microsoft Sysmon service..." -ForegroundColor Yellow
$sysmonService = Get-Service -Name "Sysmon", "Sysmon64" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($sysmonService) {
    Write-Host "  [OK] Sysmon service found: $($sysmonService.Name) (Status: $($sysmonService.Status))" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Sysmon service is not running or not installed." -ForegroundColor Red
    Write-Host "  [INFO] Download Sysmon from https://download.sysinternals.com/files/Sysmon.zip and install with:" -ForegroundColor Cyan
    Write-Host "         .\Sysmon64.exe -i -accepteula -n" -ForegroundColor Cyan
}

# 5. Final Summary
Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "                     Status Summary                    " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Procmon64.exe: $(if (Test-Path $procmonExe) { '[AVAILABLE]' } else { '[MISSING]' })" -ForegroundColor $(if (Test-Path $procmonExe) { 'Green' } else { 'Red' })
Write-Host "  Npcap Driver:  $(if ($npcapSvc) { '[AVAILABLE]' } else { '[MISSING]' })" -ForegroundColor $(if ($npcapSvc) { 'Green' } else { 'Red' })
Write-Host "  tshark.exe:    $(if (Test-Path $tsharkExe) { '[AVAILABLE]' } else { '[MISSING]' })" -ForegroundColor $(if (Test-Path $tsharkExe) { 'Green' } else { 'Red' })
Write-Host "  Sysmon:        $(if ($sysmonService -and $sysmonService.Status -eq 'Running') { '[RUNNING]' } else { '[NOT RUNNING]' })" -ForegroundColor $(if ($sysmonService -and $sysmonService.Status -eq 'Running') { 'Green' } else { 'Red' })
Write-Host "`nNext Step: If all tools are available, take a VirtualBox snapshot:" -ForegroundColor Cyan
Write-Host "  VBoxManage snapshot \"Windows\" take \"clean\"" -ForegroundColor White
