from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from adam.collectors.parsers.evtx import iter_evtx_records
from adam.sandbox.shutdown import shutdown_normally


def test_evtx_watermark_filters_before_xml_render(monkeypatch):
    import Evtx.Evtx as evtx
    records = [SimpleNamespace(record_num=lambda n=n:n, xml=Mock(return_value=f'record {n}')) for n in (1,2,3)]
    class Log:
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def records(self): return iter(records)
    monkeypatch.setattr(evtx, 'Evtx', Log)
    assert list(iter_evtx_records('unused', min_record_id=2)) == ['record 3']
    records[0].xml.assert_not_called()
    records[1].xml.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_requires_observed_poweroff_without_forcing():
    client = SimpleNamespace(get_state=AsyncMock(return_value='running'),
        wait_for_state=AsyncMock(return_value=SimpleNamespace(success=False)),stop=AsyncMock())
    channel = SimpleNamespace(run_process=AsyncMock(return_value=SimpleNamespace(return_code=0)))
    assert not await shutdown_normally(client, 'vm', channel=channel)
    channel.run_process.assert_awaited_once()
    client.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_shutdown_response_uses_authoritative_state():
    client = SimpleNamespace(get_state=AsyncMock(side_effect=['running','poweroff']),
        wait_for_state=AsyncMock(return_value=SimpleNamespace(success=True)),stop=AsyncMock())
    channel = SimpleNamespace(run_process=AsyncMock(side_effect=RuntimeError('response lost')))
    assert await shutdown_normally(client, 'vm', channel=channel)
    client.stop.assert_not_awaited()
