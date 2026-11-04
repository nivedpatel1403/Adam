#requires -Version 5.1
#requires -RunAsAdministrator
<# Run from an elevated PowerShell inside the guest after extracting the repair bundle.
The standard Npcap wizard is interactive. Complete that wizard when it opens.
This script does not create, rename, restore, or delete VM snapshots. #>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
$manifest = Get-Content -LiteralPath '.\signatures.json' -Raw | ConvertFrom-Json
foreach ($entry in $manifest) {
    if ($entry.Status -ne 'Valid' -or (Get-FileHash -LiteralPath $entry.File -Algorithm SHA256).Hash -ne $entry.SHA256) {
        throw "Staged installer failed integrity verification: $($entry.File)"
    }
}
Expand-Archive -LiteralPath '.\agent.zip' -DestinationPath '.\agent-package' -Force
$agentManifest = Get-Content '.\agent-package\build_manifest.json' -Raw | ConvertFrom-Json
foreach ($entry in $agentManifest.files.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path '.\agent-package' $entry.Name) -Algorithm SHA256).Hash -ne $entry.Value.sha256) {
        throw "Agent package hash mismatch: $($entry.Name)"
    }
}
if (-not (Get-Service npcap -ErrorAction SilentlyContinue)) {
    Write-Host 'Complete the Npcap installation wizard, then this script will continue.'
    $installer = Start-Process -FilePath (Join-Path $PSScriptRoot 'npcap-1.90.exe') -WindowStyle Normal -PassThru
    $installer.WaitForExit()
    if ($installer.ExitCode -eq 3010) { throw 'Reboot the guest, then rerun this script' }
    if ($installer.ExitCode -ne 0) { throw "Npcap installation failed: $($installer.ExitCode)" }
}
& '.\provision_guest_tools.ps1' -ProcmonSourcePath (Join-Path $PSScriptRoot 'Procmon64.exe') `
    -WiresharkInstallerPath (Join-Path $PSScriptRoot 'Wireshark-4.6.9-x64.exe') `
    -NpcapInstallerPath (Join-Path $PSScriptRoot 'npcap-1.90.exe') `
    -InstallerManifestPath (Join-Path $PSScriptRoot 'signatures.json')
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Tool provisioning failed' }
# Preserve existing authentication and other settings; change only the tool paths.
$installDir = 'C:\ADAM\agent'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$configPath = Join-Path $installDir 'agent.config.json'
if (-not (Test-Path $configPath) -and (Test-Path 'C:\ADAM\agent.config.json')) {
    Copy-Item -LiteralPath 'C:\ADAM\agent.config.json' -Destination $configPath
}
if (Test-Path $configPath) {
    $config = Get-Content $configPath -Raw | ConvertFrom-Json
    $config | Add-Member -NotePropertyName ProcmonPath -NotePropertyValue 'C:\ADAM\Tools\Procmon\Procmon64.exe' -Force
    $config | Add-Member -NotePropertyName TsharkPath -NotePropertyValue 'C:\Program Files\Wireshark\tshark.exe' -Force
    $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8
}
& '.\agent-package\install.ps1' -InstallDir $installDir
if ($LASTEXITCODE -ne 0) { throw 'Agent deployment verification failed' }
foreach ($entry in $agentManifest.files.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $installDir $entry.Name) -Algorithm SHA256).Hash -ne $entry.Value.sha256) {
        throw "Deployed agent hash mismatch: $($entry.Name)"
    }
}
Write-Host "Guest deployment verified. Expected build: $($agentManifest.build_id). Return to the host for candidate capture verification."
