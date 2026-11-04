"""Parse the actual deployable guest scripts, rather than checking text fragments."""
import shutil
import subprocess
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("relative_path", ["adam/sandbox/guest/agent", "scripts/provision_guest_tools.ps1", "scripts/finish_guest_repair.ps1"])
def test_guest_powershell_scripts_parse(relative_path):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell parser is unavailable")
    root = Path(__file__).resolve().parents[2] / relative_path
    command = r"""
$failed=$false
Get-ChildItem -LiteralPath $args[0] -Recurse -File | Where-Object { $_.Extension -in '.ps1','.psm1' } | ForEach-Object {
    $errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$null,[ref]$errors) | Out-Null
    if ($errors) { $errors | ForEach-Object { $_.ToString() }; $failed=$true }
}
if ($failed) { exit 1 }
"""
    # A temporary wrapper is unnecessary; pass the absolute root as a literal
    # escaped PowerShell string, independent of the working directory.
    command = command.replace("$args[0]", "'" + str(root).replace("'", "''") + "'")
    result = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("script_name", ["provision_guest_tools.ps1", "finish_guest_repair.ps1"])
def test_staged_installer_manifest_accepts_multiple_files_and_rejects_tampering(tmp_path, script_name):
    """Execute the real integrity gate under Windows PowerShell, without installers."""
    import hashlib

    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    entries = []
    for name in ("first.exe", "second.exe"):
        contents = name.encode()
        (tmp_path / name).write_bytes(contents)
        entries.append({"File": name, "Status": "Valid", "SHA256": hashlib.sha256(contents).hexdigest()})
    (tmp_path / "signatures.json").write_text(json.dumps(entries), encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "scripts" / script_name
    command = r"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
$ast = [System.Management.Automation.Language.Parser]::ParseFile('__SOURCE__', [ref]$null, [ref]$null)
if ('__NAME__' -eq 'provision_guest_tools.ps1') {
    $function = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-StagedInstaller'}, $true)
    Invoke-Expression $function.Extent.Text
    $InstallerManifestPath = Join-Path $PWD 'signatures.json'
    $check = { Assert-StagedInstaller (Join-Path $PWD 'first.exe'); Assert-StagedInstaller (Join-Path $PWD 'second.exe') }
} else {
    $assignment = $ast.Find({param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$manifest'}, $true)
    $loop = $ast.Find({param($node) $node -is [System.Management.Automation.Language.ForEachStatementAst] -and $node.Condition.Extent.Text -eq '$manifest'}, $true)
    $check = [scriptblock]::Create($assignment.Extent.Text + "`n" + $loop.Extent.Text)
}
& $check
[IO.File]::WriteAllText((Join-Path $PWD 'second.exe'), 'tampered')
$rejected = $false
try { & $check } catch { $rejected = $true }
if (-not $rejected) { throw 'Tampered installer was accepted' }
"""
    command = command.replace("__SOURCE__", str(source).replace("'", "''")).replace("__NAME__", script_name)
    result = subprocess.run([shell, "-NoProfile", "-Command", command], cwd=tmp_path,
                            capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
