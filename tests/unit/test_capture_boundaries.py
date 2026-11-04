from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from adam.collectors.parsers.evtx import iter_evtx_records
from adam.sandbox.shutdown import shutdown_normally


def test_evtx_watermark_filters_before_xml_render(monkeypatch):
    import Evtx.Evtx as evtx
    def xml(number):
        return f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventRecordID>{number}</EventRecordID></System></Event>'
    records = [SimpleNamespace(record_num=lambda n=n:n, xml=Mock(return_value=xml(n+999))) for n in (1,2,3,4)]
    class Log:
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def records(self): return iter(records)
    monkeypatch.setattr(evtx, 'Evtx', Log)
    assert list(iter_evtx_records('unused', min_record_id=1002)) == [xml(1003)]
    records[0].xml.assert_called_once()  # Establish the export's rebased offset.
    records[1].xml.assert_not_called()
    records[2].xml.assert_not_called()


def test_sparse_export_filters_original_ids_without_inventing_offset(monkeypatch):
    import Evtx.Evtx as evtx
    def xml(number):
        return f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventRecordID>{number}</EventRecordID></System></Event>'
    records = [SimpleNamespace(record_num=lambda n=n:n, xml=Mock(return_value=xml(original)))
               for n, original in ((1,1000),(2,1010),(3,1020))]
    class Log:
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def records(self): return iter(records)
    monkeypatch.setattr(evtx, 'Evtx', Log)
    assert list(iter_evtx_records('unused', min_record_id=1005)) == [xml(1010),xml(1020)]


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
