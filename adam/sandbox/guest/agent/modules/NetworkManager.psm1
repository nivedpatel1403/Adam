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
    param(
        [Parameter(Mandatory = $true)][string]$TsharkPath,
        [Parameter(Mandatory = $false)][object[]]$Adapters = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces())
    )
    try {
        # Npcap uses the Windows adapter GUID directly. Resolving it does
        # not require launching a second, potentially cold dumpcap process.
        foreach ($nic in $Adapters) {
            if ($nic.OperationalStatus -ne [Net.NetworkInformation.OperationalStatus]::Up) { continue }
            $hostOnly = @($nic.GetIPProperties().UnicastAddresses | Where-Object { $_.Address.ToString().StartsWith('192.168.19.') })
            if ($hostOnly.Count -eq 0) { continue }
            $InterfaceGuid = [Guid]::Empty
            if ([Guid]::TryParse($nic.Id, [ref]$InterfaceGuid)) {
                return '\Device\NPF_' + $InterfaceGuid.ToString('B').ToUpperInvariant()
            }
        }
    } catch { }
    return $null
}

function Get-NetworkInterfaces {
    param([Parameter(Mandatory = $true)][string]$TsharkPath)
    if (-not (Test-Path -LiteralPath $TsharkPath)) {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage "tshark not found at $TsharkPath"
    }
    try {
        $dumpcap = Join-Path (Split-Path -Parent $TsharkPath) 'dumpcap.exe'
        if (-not (Test-Path -LiteralPath $dumpcap)) { return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage 'dumpcap capture component is missing' }
        $result = Invoke-NativeProcess -FilePath $dumpcap -ArgumentList @('-D') -TimeoutMs 45000
        if ($result.TimedOut) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.Timeout -ErrorMessage "dumpcap -D did not complete in time"
        }
        if ($result.ExitCode -ne 0) { return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage $result.StdErr }
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
        [Parameter(Mandatory = $true)][string]$PcapPath,
        [Parameter(Mandatory = $false)][int]$CaptureReadyTimeoutMs = 90000
    )
    if (-not (Test-Path -LiteralPath $TsharkPath)) {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.ToolUnavailable -ErrorMessage "tshark not found at $TsharkPath"
    }
    try {
        if ($Script:RunningCaptures.ContainsKey($SessionId) -or (Test-Path -LiteralPath $PcapPath)) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InvalidArgument -ErrorMessage 'Capture session or output already exists'
        }
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
        $Script:RunningCaptures[$SessionId] = @{ Process = $result.Process; Children = @() }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $ready = $false
        # A live tshark PID is not capture readiness. On a cold Windows boot
        # plugin/driver initialization can take tens of seconds before dumpcap
        # creates the pcapng section and interface blocks (at least 48 bytes).
        while ($clock.ElapsedMilliseconds -lt $CaptureReadyTimeoutMs) {
            if ($result.Process.HasExited) { throw 'tshark exited before capture became ready' }
            if ((Test-Path -LiteralPath $PcapPath) -and (Get-Item -LiteralPath $PcapPath).Length -ge 48) {
                $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($result.Pid) AND Name='dumpcap.exe'" | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
                $Script:RunningCaptures[$SessionId].Children = $children
                if ($children.Count -gt 0) { $ready = $true; break }
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw 'Capture readiness deadline exceeded before pcapng interface initialization' }
        Write-AgentLog -Message "session=$SessionId tshark started pid=$($result.Pid) interface=$targetInterface pcap=$PcapPath"
        return New-SuccessEnvelope -Data @{ pid = $result.Pid }
    } catch {
        $problem = $_.Exception.Message
        if ($Script:RunningCaptures.ContainsKey($SessionId)) {
            $cleanup = Invoke-NetworkStop -SessionId $SessionId
            if (-not $cleanup.success) { $problem += "; cleanup failed: $($cleanup.error_message)" }
        }
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $problem
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
            if ($proc -and -not $proc.HasExited) {
                # A startup timeout may occur after dumpcap was spawned but
                # before its output became ready. Discover only this owned
                # parent's capture children before terminating the parent.
                $capture.Children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id) AND Name='dumpcap.exe'" | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
            }
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
