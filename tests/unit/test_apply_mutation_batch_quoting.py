"""
tests/unit/test_apply_mutation_batch_quoting.py

Regression test verifying that apply_mutation_batch in HTTPGuestChannel
correctly encodes commands and file manifests using PowerShell's -EncodedCommand
and Base64 JSON payloads, avoiding any cmd.exe quote-stripping issues.
"""

from __future__ import annotations

import base64
import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from adam.sandbox.guest.channel import InMemoryGuestChannel
from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.vbox.models import VMOperationResult


@pytest.mark.asyncio
async def test_apply_mutation_batch_powershell_encoded_command() -> None:
    """
    Test that apply_mutation_batch uses -EncodedCommand with Base64 JSON payload
    and preserves problematic characters (quotes, apostrophes, backslashes, semicolons).
    """
    channel = HTTPGuestChannel(
        "http://127.0.0.1:8765",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path=None,
        tshark_path=None,
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
    )

    test_files = [
        ("C:/Users/User/Desktop/test 1's \"secret\".txt", "line1; line2 'quote' \"double\""),
        ("C:\\Program Files (x86)\\app\\config.json", "{\"key\": \"val;with;semicolons\"}"),
        ("D:/special_chars/`~!@#$%^&*()_+-=[]{}\\|;':\",.<>?.log", "data"),
    ]

    mock_result = VMOperationResult(
        success=True,
        command=("powershell.exe",),
        duration_ms=10.0,
        return_code=0,
        stdout="success",
        stderr="",
        termination_reason="STATUS_SUCCESS",
    )

    with patch.object(channel, "run_process", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_result
        await channel.apply_mutation_batch(test_files, timeout_s=45.0)

        mock_run.assert_awaited_once()
        args = mock_run.call_args[0]
        binary = args[0]
        cmd_args = args[1]
        kwargs = mock_run.call_args[1]

        assert "powershell.exe" in binary.lower()
        assert "-EncodedCommand" in cmd_args
        assert kwargs.get("wait") is True
        assert kwargs.get("timeout_s") == 45.0

        idx = cmd_args.index("-EncodedCommand")
        encoded_cmd = cmd_args[idx + 1]

        # 1. Decode PowerShell UTF-16LE script
        script_bytes = base64.b64decode(encoded_cmd)
        ps_script = script_bytes.decode("utf-16-le")

        # 2. Extract base64 JSON payload
        match = re.search(r"\$b64\s*=\s*'([^']+)';", ps_script)
        assert match is not None, f"Could not find $b64 in script: {ps_script}"
        b64_json = match.group(1)

        # 3. Decode JSON manifest
        json_bytes = base64.b64decode(b64_json)
        manifest = json.loads(json_bytes.decode("utf-8"))

        assert len(manifest) == len(test_files)
        for i, (orig_path, orig_content) in enumerate(test_files):
            expected_norm_path = orig_path.replace("/", "\\")
            assert manifest[i]["path"] == expected_norm_path
            assert manifest[i]["content"] == orig_content


@pytest.mark.asyncio
async def test_apply_mutation_batch_empty_raises_value_error() -> None:
    channel = HTTPGuestChannel(
        "http://127.0.0.1:8765",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path=None,
        tshark_path=None,
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
    )
    with pytest.raises(ValueError, match="must not be empty"):
        await channel.apply_mutation_batch([])


@pytest.mark.asyncio
async def test_in_memory_guest_channel_apply_mutation_batch() -> None:
    channel = InMemoryGuestChannel()
    files = [("C:\\test.txt", "val1"), ("C:\\test2.txt", "val2")]

    await channel.apply_mutation_batch(files)

    assert channel.applied_batches == [files]
    assert ("FILE", "C:\\test.txt", "CREATE", "val1") in channel.applied_mutations
    assert ("FILE", "C:\\test2.txt", "CREATE", "val2") in channel.applied_mutations
