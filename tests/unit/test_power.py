import asyncio

import pytest

from adam.common import power


@pytest.fixture
def windows_request(monkeypatch):
    calls = []
    monkeypatch.setattr(power, "_WINDOWS", True)
    def request(flags):
        calls.append(flags)
        return 0x80000002  # Preserve a pre-existing display request.
    monkeypatch.setattr(power, "_execution_state", request)
    return calls


@pytest.mark.asyncio
async def test_overlapping_runs_hold_request_until_last_run_finishes(windows_request):
    first_entered, second_entered = asyncio.Event(), asyncio.Event()
    release_first, release_second = asyncio.Event(), asyncio.Event()
    @power.keep_system_awake
    async def run(entered, release):
        entered.set()
        await release.wait()
    first = asyncio.create_task(run(first_entered, release_first))
    await first_entered.wait()
    second = asyncio.create_task(run(second_entered, release_second))
    await second_entered.wait()
    release_first.set()
    await first
    assert windows_request == [0x80000001]
    release_second.set()
    await second
    assert windows_request == [0x80000001, 0x80000002]


@pytest.mark.asyncio
async def test_cancellation_releases_request(windows_request):
    entered = asyncio.Event()
    @power.keep_system_awake
    async def run():
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(run())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert windows_request == [0x80000001, 0x80000002]


def test_non_windows_does_not_call_windows_api(monkeypatch):
    monkeypatch.setattr(power, "_WINDOWS", False)
    monkeypatch.setattr(power, "_execution_state", lambda _: pytest.fail("Windows API called"))
    with power.system_awake():
        pass


def test_failed_acquisition_does_not_run_work(monkeypatch):
    monkeypatch.setattr(power, "_WINDOWS", True)
    def fail(_):
        raise OSError("request rejected")
    monkeypatch.setattr(power, "_execution_state", fail)
    with pytest.raises(OSError, match="request rejected"), power.system_awake():
        pytest.fail("Work ran without its power request")
