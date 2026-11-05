"""Status probes must stop their own stalled subprocesses without touching the VM."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from adam.sandbox.vbox.client import VBoxCommandError, VirtualBoxClient


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_status_probe_reaps_stalled_process(monkeypatch, cancel):
    started = asyncio.Event()

    async def communicate():
        started.set()
        await asyncio.Event().wait()

    process = SimpleNamespace(returncode=None, communicate=communicate,
                              kill=Mock(), wait=AsyncMock(return_value=-1))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    client = VirtualBoxClient(default_command_timeout=0.01 if not cancel else 30)
    task = asyncio.create_task(client.get_state("Windows"))
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(VBoxCommandError, match="timed out"):
            await task
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
