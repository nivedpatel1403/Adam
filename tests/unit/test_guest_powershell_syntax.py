"""Parse the actual deployable guest scripts, rather than checking text fragments."""
import shutil
import subprocess
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
