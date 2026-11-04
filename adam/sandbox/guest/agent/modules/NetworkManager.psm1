#requires -Version 5.1
<#
.SYNOPSIS
    NetworkManager.psm1 -- implements docs/phase5-http-agent-api.md
    section 7 (/network/*), the tshark-backed capture/convert manager.

.DESCRIPTION
    /network/convert is the native fix for Issue #1 (the compatibility
    backend's "'C:\Program' is not recognized" bug): tshark.exe is
    launched with ArgumentList = @('-r', $PcapPath, '-T', 'ek') and its
    StandardOutput stream is written directly to $EkJsonPath by
    Invoke-NativeProcess -RedirectStandardOutputTo -- no cmd.exe, no `>`,
    no argument ever needs manual quoting regardless of spaces in
    $TsharkPath, because FileName is never part of a parsed command-line
    string.
#>

Set-StrictMode -Version Latest
# No -Force: Common.psm1 is already imported once by adam_agent.ps1 at
# top level; -Force here would remove that instance's exports from the
# top-level script's scope and re-add them only nested in this module,
# invisible to adam_agent.ps1 itself (PowerShell/PowerShell#7367) -- see
# ArtifactManager.psm1's own comment on this same line for the full
# explanation; this was a real, shipped startup crash
# ("Write-AgentLog is not recognized").
Import-Module (Join-Path $PSScriptRoot 'Common.psm1')

$Script:RunningCaptures = @{}

function Resolve-HostOnlyInterfaceIndex {
    param([Parameter(Mandatory = $true)][string]$TsharkPath)

    if (-not (Test-Path -LiteralPath $TsharkPath)) { return $null }
    try {
        $result = Invoke-NativeProcess -FilePath $TsharkPath -ArgumentList @('-D') -TimeoutMs 15000
        if ($result.TimedOut -or [string]::IsNullOrWhiteSpace($result.StdOut)) { return $null }

        $lines = $result.StdOut -split "`r?`n"
        $interfaces = @()
        foreach ($line in $lines) {
            if ($line -match '^(\d+)\.\s+(.*)$') {
                $interfaces += @{ index = $Matches[1]; description = $Matches[2] }
            }
        }
        if ($interfaces.Count -eq 0) { return $null }

        # Check for network adapter on 192.168.19.*
        $hostOnlyAlias = $null
        try {
            $ipObj = Get-NetIPAddress -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -like '192.168.19.*' } | Select-Object -First 1
            if ($ipObj) {
                $hostOnlyAlias = $ipObj.InterfaceAlias
                $adapter = Get-NetAdapter -InterfaceIndex $ipObj.InterfaceIndex -ErrorAction Stop
                $guid = $adapter.InterfaceGuid.ToString().Trim('{}')
                foreach ($iface in $interfaces) {
                    if ($iface.description -match [regex]::Escape($guid)) { return $iface.index }
                }
            }
        } catch { }

        if ($hostOnlyAlias) {
            foreach ($iface in $interfaces) {
                if ($iface.description -like "*$hostOnlyAlias*" -or $iface.description -match [regex]::Escape($hostOnlyAlias)) {
                    return $iface.index
                }
            }
        }

        return $null
    } catch {
        return $null
    }
}

function Get-NetworkInterfaces {
    param([Parameter(Mandatory = $true)][string]$TsharkPath)
    if (-not (Test-Path -LiteralPath $TsharkPath)) {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage "tshark not found at $TsharkPath"
    }
    try {
        $result = Invoke-NativeProcess -FilePath $TsharkPath -ArgumentList @('-D') -TimeoutMs 15000
        if ($result.TimedOut) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.Timeout -ErrorMessage "tshark -D did not complete in time"
        }
        $interfaces = @()
        foreach ($line in ($result.StdOut -split "`r?`n")) {
            if ($line -match '^(\d+)\.\s+(.*)$') {
                $interfaces += @{ index = $Matches[1]; description = $Matches[2] }
            }
        }
        return New-SuccessEnvelope -Data @{ interfaces = $interfaces }
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    }
}

function Invoke-NetworkStart {
    param(
        [Parameter(Mandatory = $true)][string]$TsharkPath,
        [Parameter(Mandatory = $true)][string]$SessionId,
        [Parameter(Mandatory = $false)][string]$Interface,
        [Parameter(Mandatory = $true)][string]$PcapPath
    )
    if (-not (Test-Path -LiteralPath $TsharkPath)) {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage "tshark not found at $TsharkPath"
    }
    try {
        $targetInterface = $Interface
        $matchedIndex = Resolve-HostOnlyInterfaceIndex -TsharkPath $TsharkPath
        if ($targetInterface -eq 'auto' -and -not $matchedIndex) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage 'Host-only capture interface could not be resolved'
        }
        if ($matchedIndex) {
            if ([string]::IsNullOrWhiteSpace($targetInterface) -or $targetInterface -eq 'auto') {
                $targetInterface = $matchedIndex
            } else {
                # If explicit interface was provided (e.g. '1'), check whether it points to loopback
                $ifacesResult = Get-NetworkInterfaces -TsharkPath $TsharkPath
                if ($ifacesResult.success -and $ifacesResult.data.interfaces) {
                    $selected = $ifacesResult.data.interfaces | Where-Object { $_.index -eq $targetInterface }
                    if ($selected -and $selected.description -match 'loopback|Loopback') {
                        Write-AgentLog -Level WARN -Message "Provided interface index $targetInterface is loopback; dynamically redirecting to host-only adapter $matchedIndex"
                        $targetInterface = $matchedIndex
                    }
                }
            }
        }
        if ([string]::IsNullOrWhiteSpace($targetInterface)) {
            $targetInterface = if ($matchedIndex) { $matchedIndex } else { '1' }
        }

        $args = @('-i', $targetInterface, '-w', $PcapPath)
        $result = Invoke-NativeProcess -FilePath $TsharkPath -ArgumentList $args -TimeoutMs $null
        if ($null -eq $result.Pid) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $result.StdErr
        }
        Start-Sleep -Milliseconds 500
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($result.Pid) AND Name='dumpcap.exe'" | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
        if ($result.Process.HasExited) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage "tshark exited before capture became ready"
        }
        $Script:RunningCaptures[$SessionId] = @{ Process = $result.Process; Children = $children }
        Write-AgentLog -Message "session=$SessionId tshark started pid=$($result.Pid) interface=$targetInterface pcap=$PcapPath"
        return New-SuccessEnvelope -Data @{ pid = $result.Pid }
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    }
}

function Invoke-NetworkStop {
    param([Parameter(Mandatory = $true)][string]$SessionId)
    try {
        # tshark spawns a privileged dumpcap.exe helper that actually
        # performs the capture -- stop gracefully to flush capture buffers.
        $stoppedAny = $false
        if ($Script:RunningCaptures.ContainsKey($SessionId)) {
            $capture = $Script:RunningCaptures[$SessionId]
            $proc = $capture.Process
            if ($proc) {
                try {
                    $proc.CloseMainWindow() | Out-Null
                    $proc.WaitForExit(2000) | Out-Null
                } catch { }
                if (-not $proc.HasExited) {
                    try { $proc.Kill() } catch { }
                    try { $proc.WaitForExit(2000) | Out-Null } catch { }
                }
                $stoppedAny = $true
            }
        foreach ($dumpcap in $capture.Children) {
            try {
                $dumpcap.CloseMainWindow() | Out-Null
                $dumpcap.WaitForExit(2000) | Out-Null
            } catch { }
            if (-not $dumpcap.HasExited) {
                try { $dumpcap.Kill(); $stoppedAny = $true } catch { }
                try { $dumpcap.WaitForExit(2000) | Out-Null } catch { }
            } else {
                $stoppedAny = $true
            }
        }
            $Script:RunningCaptures.Remove($SessionId) | Out-Null
        }
        # Allow buffer flush to disk
        Start-Sleep -Milliseconds 500
        return New-SuccessEnvelope -Data @{ stopped = $stoppedAny }
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    }
}

function Invoke-NetworkConvert {
    param(
        [Parameter(Mandatory = $true)][string]$TsharkPath,
        [Parameter(Mandatory = $true)][string]$PcapPath,
        [Parameter(Mandatory = $true)][string]$EkJsonPath
    )
    if (-not (Test-Path -LiteralPath $TsharkPath)) {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage "tshark not found at $TsharkPath"
    }
    if (-not (Test-Path -LiteralPath $PcapPath)) {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.NotFound -ErrorMessage "capture file not found: $PcapPath"
    }
    try {
        # Issue #1 fix, native form: '-r', pcap, '-T', 'ek' as their own
        # ArgumentList entries; stdout streamed straight to $EkJsonPath.
        # No cmd.exe, no '>' operator, nothing to mis-quote.
        $args = @('-r', $PcapPath, '-T', 'ek')
        $result = Invoke-NativeProcess -FilePath $TsharkPath -ArgumentList $args -TimeoutMs 60000 -RedirectStandardOutputTo $EkJsonPath
        if ($result.TimedOut) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.Timeout -ErrorMessage "EK JSON conversion did not complete in time"
        }
        if ($result.ExitCode -ne 0) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage "EK JSON conversion failed with exit code $($result.ExitCode): $($result.StdErr)"
        }
        if (-not (Test-Path -LiteralPath $EkJsonPath)) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage "conversion reported completion but $EkJsonPath does not exist"
        }
        $ekItem = Get-Item -LiteralPath $EkJsonPath
        if ($ekItem.Length -le 0) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage "conversion produced an empty EK JSON file ($EkJsonPath has 0 bytes)"
        }
        return New-SuccessEnvelope -Data @{ ek_json_path = $EkJsonPath }
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    }
}

Export-ModuleMember -Function Get-NetworkInterfaces, Invoke-NetworkStart, Invoke-NetworkStop, Invoke-NetworkConvert
