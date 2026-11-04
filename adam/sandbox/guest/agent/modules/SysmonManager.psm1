#requires -Version 5.1
<#
.SYNOPSIS
    SysmonManager.psm1 -- implements docs/phase5-http-agent-api.md
    section 8 (/sysmon/*).

.DESCRIPTION
    /sysmon/export tries `wevtutil.exe epl` first, then falls back to a
    raw copy of the channel's own backing .evtx file on ACCESS_DENIED --
    the native reimplementation of agent.py's
    `_export_sysmon_raw_copy_fallback()` (Issue #3), reporting which
    mechanism actually worked via the `mechanism` field so the host side
    never has to guess.
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

function Invoke-SysmonExport {
    param(
        [Parameter(Mandatory = $true)][string]$Channel,
        [Parameter(Mandatory = $true)][string]$OutputPath
    )
    try {
        $wevtutil = Join-Path $env:WINDIR 'System32\wevtutil.exe'
        $result = Invoke-NativeProcess -FilePath $wevtutil -ArgumentList @('epl', $Channel, $OutputPath) -TimeoutMs 15000

        if (-not $result.TimedOut -and $result.ExitCode -eq 0 -and (Test-Path -LiteralPath $OutputPath)) {
            Write-AgentLog -Message "sysmon export via wevtutil epl succeeded: $OutputPath"
            return New-SuccessEnvelope -Data @{ output_path = $OutputPath; mechanism = 'wevtutil' }
        }

        Write-AgentLog -Level WARN -Message "wevtutil epl failed (exit_code=$($result.ExitCode) stderr=$($result.StdErr)) -- trying raw-copy fallback"

        # Issue #3 fallback: copy the channel's own backing .evtx file
        # directly. Standard "Applications and Services Logs" naming
        # convention: forward slashes in the channel name become '%4' in
        # the file name (Microsoft's own Event Log architecture, not
        # Sysmon-specific).
        $rawSourcePath = Join-Path $env:WINDIR "System32\winevt\Logs\$($Channel -replace '/', '%4').evtx"
        if (-not (Test-Path -LiteralPath $rawSourcePath)) {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.AccessDenied -ErrorMessage "wevtutil epl failed and raw log file not found at $rawSourcePath. wevtutil stderr: $($result.StdErr)"
        }
        try {
            Copy-Item -LiteralPath $rawSourcePath -Destination $OutputPath -Force
        } catch {
            return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.AccessDenied -ErrorMessage "both wevtutil epl and a raw file copy were denied: $($_.Exception.Message)"
        }
        Write-AgentLog -Message "sysmon export via raw copy fallback succeeded: $rawSourcePath -> $OutputPath"
        return New-SuccessEnvelope -Data @{ output_path = $OutputPath; mechanism = 'raw_copy' }
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    }
}

function Get-SysmonDiagnostics {
    param([Parameter(Mandatory = $true)][string]$Channel)
    try {
        $available = $true
        $eventCount = $null
        try {
            $wevtutil = Join-Path $env:WINDIR 'System32\wevtutil.exe'
            $gli = Invoke-NativeProcess -FilePath $wevtutil -ArgumentList @('gli', $Channel) -TimeoutMs 5000
            if (-not $gli.TimedOut -and $gli.ExitCode -eq 0) {
                $available = $true
                if ($gli.StdOut -match 'numberOfLogRecords:\s*(\d+)') {
                    $eventCount = [int]$matches[1]
                }
            } else {
                $log = Get-WinEvent -ListLog $Channel -ErrorAction Stop
                $eventCount = $log.RecordCount
            }
        } catch [System.Diagnostics.Eventing.Reader.EventLogNotFoundException] {
            $available = $false
        } catch {
            $available = $false
        }
        return New-SuccessEnvelope -Data @{ channel_available = $available; event_count = $eventCount }
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    }
}

function Get-SysmonEventsSince {
    param(
        [Parameter(Mandatory = $false)][long]$RecordId = 0,
        [Parameter(Mandatory = $false)][string]$EventIds = '1,3',
        [Parameter(Mandatory = $false)][string]$Channel = 'Microsoft-Windows-Sysmon/Operational'
    )
    $reader = $null
    $latestReader = $null
    try {
        $idParts = @()
        foreach ($id in $EventIds.Split(',')) {
            $trimmed = $id.Trim()
            if ($trimmed -notmatch '^\d+$') {
                return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InvalidArgument -ErrorMessage 'EventIds must be comma-separated integers'
            }
            $idParts += "EventID=$([int]$trimmed)"
        }
        $latestQuery = New-Object System.Diagnostics.Eventing.Reader.EventLogQuery($Channel, [System.Diagnostics.Eventing.Reader.PathType]::LogName, '*')
        $latestQuery.ReverseDirection = $true
        $latestReader = New-Object System.Diagnostics.Eventing.Reader.EventLogReader($latestQuery)
        $latest = $latestReader.ReadEvent()
        $currentMaxId = 0L
        if ($latest) {
            try { $currentMaxId = [long]$latest.RecordId } finally { $latest.Dispose() }
        }
        if ($RecordId -lt 0) {
            return New-SuccessEnvelope -Data @{last_record_id=$currentMaxId;log_reset=$false;events=@()}
        }
        $logReset = $RecordId -gt 0 -and $currentMaxId -lt $RecordId
        $cursor = if ($logReset) { 0L } else { $RecordId }
        if ($cursor -ge $currentMaxId) {
            return New-SuccessEnvelope -Data @{last_record_id=$cursor;log_reset=[bool]$logReset;events=@()}
        }
        $idClause = '(' + ($idParts -join ' or ') + ')'
        $xpath = "*[System[$idClause and EventRecordID > $cursor]]"
        $query = New-Object System.Diagnostics.Eventing.Reader.EventLogQuery($Channel, [System.Diagnostics.Eventing.Reader.PathType]::LogName, $xpath)
        $reader = New-Object System.Diagnostics.Eventing.Reader.EventLogReader($query)
        $events = New-Object 'System.Collections.Generic.List[object]'
        $maxRecordId = $cursor
        $clock = [Diagnostics.Stopwatch]::StartNew()
        # One page only. Never drain a growing log inside the single-threaded
        # HTTP handler. The host schedules subsequent pages cooperatively.
        while ($events.Count -lt 100 -and $clock.Elapsed.TotalSeconds -lt 5) {
            try {
                $remaining = [Math]::Max(0.001, 5 - $clock.Elapsed.TotalSeconds)
                $record = $reader.ReadEvent([TimeSpan]::FromSeconds($remaining))
            } catch {
                # EvtNext can time out after returning a short page. Preserve
                # that page and its actual cursor; never discard read events
                # or fabricate an empty batch on a failed initial read.
                $native = $_.Exception.GetBaseException()
                if (($native.HResult -band 0xFFFF) -eq 1460 -and $events.Count -gt 0) { break }
                throw
            }
            if (-not $record) { break }
            try {
                $xml = [xml]$record.ToXml()
                $data = @{}
                if ($xml.Event.EventData) {
                    foreach ($item in @($xml.Event.EventData.Data)) {
                        if ($item.Name) { $data[[string]$item.Name] = [string]$item.InnerText }
                    }
                }
                $events.Add(@{record_id=[long]$record.RecordId;event_id=[int]$record.Id;
                    time_created=$record.TimeCreated.ToUniversalTime().ToString('o');data=$data})
                $maxRecordId = [long]$record.RecordId
            } finally { $record.Dispose() }
            if ($maxRecordId -ge $currentMaxId) { break }
        }
        # Advance only over records actually returned. In particular, a reset
        # must not jump to the latest ID and silently skip later pages.
        return New-SuccessEnvelope -Data @{last_record_id=$maxRecordId;log_reset=[bool]$logReset;events=@($events.ToArray())}
    } catch {
        return New-ErrorEnvelope -ErrorCode $Script:ErrorCodes.InternalError -ErrorMessage $_.Exception.Message
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($latestReader) { $latestReader.Dispose() }
    }
}

Export-ModuleMember -Function Invoke-SysmonExport, Get-SysmonDiagnostics, Get-SysmonEventsSince
