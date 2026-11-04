import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["delayed", "timeout", "existing"])
def test_native_capture_start_waits_for_output_and_cleans_owned_children(tmp_path, scenario):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell unavailable")
    source = Path(__file__).resolve().parents[2] / "adam/sandbox/guest/agent/modules/NetworkManager.psm1"
    tool, capture = tmp_path / "tool.exe", tmp_path / "capture.pcapng"
    tool.write_text("Never executed: native launch is replaced below")
    command = r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$null,[ref]$null)
foreach($name in @('Invoke-NetworkStart','Invoke-NetworkStop')){
 $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 Invoke-Expression $fn.Extent.Text
}
$Script:RunningCaptures=@{}
$Script:ErrorCodes=@{ToolUnavailable='TOOL';InvalidArgument='ARGUMENT';InternalError='INTERNAL'}
$Script:Ticks=0; $Script:Launches=0; $Script:Filters=@()
$Script:Scenario='__SCENARIO__'; $Script:Capture='__CAPTURE__'
$Script:Root=[pscustomobject]@{Id=100;HasExited=$false}
$Script:Child=[pscustomobject]@{Id=101;HasExited=$false}
foreach($proc in @($Script:Root,$Script:Child)){
 $proc | Add-Member ScriptMethod CloseMainWindow {return $false}
 $proc | Add-Member ScriptMethod WaitForExit {param($ms) return $this.HasExited}
 $proc | Add-Member ScriptMethod Kill {$this.HasExited=$true}
}
function New-SuccessEnvelope {param($Data) return @{success=$true;data=$Data}}
function New-ErrorEnvelope {param($ErrorCode,$ErrorMessage) return @{success=$false;error_message=$ErrorMessage}}
function Write-AgentLog {param($Message,$Level)}
function Resolve-HostOnlyInterfaceIndex {param($TsharkPath) return 'owned-interface'}
function Invoke-NativeProcess {param($FilePath,$ArgumentList,$TimeoutMs)
 $Script:Launches++; return @{Pid=100;Process=$Script:Root}
}
function Get-CimInstance {param($ClassName,$Filter)
 $Script:Filters += $Filter
 return [pscustomobject]@{ProcessId=101}
}
function Get-Process {param($Id) return $Script:Child}
function Start-Sleep {param($Milliseconds)
 $Script:Ticks++
 if($Script:Scenario -eq 'delayed' -and $Script:Ticks -eq 3){[IO.File]::WriteAllBytes($Script:Capture,(New-Object byte[] 48))}
 [Threading.Thread]::Sleep(10)
}
if($Script:Scenario -eq 'existing'){[IO.File]::WriteAllBytes($Script:Capture,(New-Object byte[] 48))}
$result=Invoke-NetworkStart -TsharkPath '__TOOL__' -SessionId test -Interface auto -PcapPath $Script:Capture -CaptureReadyTimeoutMs 200
$before=@{success=$result.success;ticks=$Script:Ticks;root_stopped=$Script:Root.HasExited;child_stopped=$Script:Child.HasExited;registered=$Script:RunningCaptures.Count;launches=$Script:Launches;filters=@($Script:Filters)}
if($Script:RunningCaptures.Count){$null=Invoke-NetworkStop -SessionId test}
$before | ConvertTo-Json -Compress
"""
    for key, value in {"SOURCE": source, "CAPTURE": capture, "TOOL": tool, "SCENARIO": scenario}.items():
        command = command.replace("__" + key + "__", str(value).replace("'", "''"))
    result = subprocess.run([shell, "-NoProfile", "-Command", command], check=False, capture_output=True,
                            text=True, errors="replace", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert all("ParentProcessId=100 AND Name='dumpcap.exe'" == f for f in observed["filters"])
    if scenario == "delayed":
        assert observed["success"] and observed["ticks"] >= 3
        assert observed["registered"] == 1 and not observed["root_stopped"]
    elif scenario == "timeout":
        assert not observed["success"] and observed["registered"] == 0
        assert observed["root_stopped"] and observed["child_stopped"]
    else:
        assert not observed["success"] and observed["launches"] == 0
        assert not observed["root_stopped"] and not observed["child_stopped"]
