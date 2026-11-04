"""Fail-closed evidence and guest-state checks used by real VM acceptance."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
RESET_FILE = r"C:\ADAM\restore_witness.txt"
RESET_KEY = r"HKLM:\Software\ADAM\RestoreWitness"
REQUIRED_CHECKS = (
    "repository", "deployment", "functional_capture", "failure_missing_tool",
    "failure_polling", "failure_timeout", "failure_export", "persistence_regressions",
    "cli_api", "dashboard",
)


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def powershell(channel, script: str, timeout: float = 30):
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = await channel.run_process(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        wait=True, timeout_s=timeout,
    )
    if result.return_code != 0:
        raise RuntimeError(result.stderr or f"Guest command failed: {result.return_code}")
    return result.stdout


async def inspect_markers(channel, marker_file: str, reg_path: str) -> dict:
    """Read actual contents; access/query failures never mean absent."""
    key = reg_path.replace("HKLM\\", "HKLM:\\")
    script = f"""
$ErrorActionPreference = 'Stop'
$file = {ps_literal(marker_file)}
$key = {ps_literal(key)}
$filePresent = Test-Path -LiteralPath $file -ErrorAction Stop
$keyPresent = Test-Path -LiteralPath $key -ErrorAction Stop
$fileValue = $null
$regValue = $null
if ($filePresent) {{ $fileValue = [IO.File]::ReadAllText($file).Trim() }}
if ($keyPresent) {{ $regValue = (Get-ItemProperty -LiteralPath $key -Name Marker -ErrorAction Stop).Marker }}
@{{file_present=$filePresent;reg_present=$keyPresent;file_value=$fileValue;reg_value=$regValue}} | ConvertTo-Json -Compress
"""
    try:
        result = json.loads(await powershell(channel, script))
        if not isinstance(result, dict) or any(type(result.get(k)) is not bool for k in ("file_present", "reg_present")):
            raise ValueError("Malformed marker observation")
        return result
    except Exception as exc:
        logger.exception("Guest marker observation failed")
        return {"error": str(exc)}


def markers_match(observation: dict, token: str) -> bool:
    return bool("error" not in observation and observation.get("file_present") is True
                and observation.get("reg_present") is True
                and observation.get("file_value") == token and observation.get("reg_value") == token)


def markers_absent(observation: dict) -> bool:
    return ("error" not in observation and observation.get("file_present") is False
            and observation.get("reg_present") is False)


async def write_markers(channel, marker_file: str, reg_path: str, token: str) -> bool:
    key = reg_path.replace("HKLM\\", "HKLM:\\")
    try:
        await powershell(channel, f"""
$ErrorActionPreference = 'Stop'
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName({ps_literal(marker_file)})) | Out-Null
[IO.File]::WriteAllText({ps_literal(marker_file)}, {ps_literal(token)})
New-Item -Path {ps_literal(key)} -Force | Out-Null
New-ItemProperty -LiteralPath {ps_literal(key)} -Name Marker -Value {ps_literal(token)} -PropertyType String -Force | Out-Null
""")
        return markers_match(await inspect_markers(channel, marker_file, key), token)
    except Exception:
        logger.exception("Guest marker write failed")
        return False


def validate_acceptance(path: str | Path | None, candidate_uuid: str) -> dict:
    """Require all external gates, matching revision/build and immutable evidence."""
    result = {"success": False}
    try:
        if not path:
            raise ValueError("Promotion requires an acceptance record")
        record_path = Path(path).resolve()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        if dirty:
            raise ValueError("Promotion requires a clean, tested repository revision")
        build = json.loads((ROOT / "adam/sandbox/guest/agent/build_manifest.json").read_text(encoding="utf-8"))["build_id"]
        for key, expected in (("candidate_uuid", candidate_uuid), ("repository_revision", revision), ("guest_build", build)):
            if record.get(key) != expected:
                raise ValueError(f"Acceptance record does not match {key}")
        for name in REQUIRED_CHECKS:
            check = record.get("checks", {}).get(name, {})
            if check.get("status") != "passed" or not check.get("evidence"):
                raise ValueError(f"Mandatory acceptance check missing or failed: {name}")
            for evidence in check["evidence"]:
                file = (record_path.parent / evidence["path"]).resolve()
                with file.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != evidence["sha256"].lower():
                    raise ValueError(f"Evidence changed: {file.name}")
        result.update(success=True, record=str(record_path), repository_revision=revision, guest_build=build)
    except Exception as exc:
        logger.exception("Acceptance record validation failed")
        result["error"] = str(exc)
    return result
