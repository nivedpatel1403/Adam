import asyncio
import json
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adam.collectors.base import BaseCollector
from adam.collectors.sysmon import SysmonCollector
from adam.common.bus import EventBus
from adam.common.config import Settings
from adam.contracts.enums import Category, SessionStatus, Source
from adam.contracts.raw_event import RawEvent
from adam.contracts.session import SampleRef
from adam.orchestrator.persistence import RawEventWriter
from adam.orchestrator.session import SessionOrchestrator, build_collectors_from_telemetry
from adam.sandbox.guest.agent.agent import TelemetryArtifacts, ToolAvailability


def event(number):
    now = datetime.now(UTC)
    return RawEvent(event_id=f"raw_{number}", session_id="batch-test", source=Source.PROCMON,
                    source_event_id=number, category=Category.FILE, occurred_at=now, observed_at=now)


class ExportSource(BaseCollector):
    source_name = "procmon"
    def __init__(self, total, capacity=2):
        super().__init__(buffer_size=capacity)
        self.total = total
    async def _run(self):
        for number in range(self.total):
            await self._emit_wait(event(number))
        self.read_complete.set()


@pytest.mark.asyncio
async def test_exported_sysmon_is_read_once_while_live_collector_keeps_polling(monkeypatch):
    exported = build_collectors_from_telemetry(
        'batch-test', TelemetryArtifacts(sysmon_evtx_path='static.evtx')
    )[0]
    live = SysmonCollector('live.evtx', session_id='batch-test', poll_interval=0.001)
    reads = {'static': 0, 'live': 0}

    def read(name):
        reads[name] += 1
        return []

    monkeypatch.setattr(exported, '_read_current_records', lambda: read('static'))
    monkeypatch.setattr(live, '_read_current_records', lambda: read('live'))
    await exported.start()
    await live.start()
    try:
        await asyncio.wait_for(exported.read_complete.wait(), timeout=1)
        for _ in range(100):
            if reads['live'] >= 2:
                break
            await asyncio.sleep(0.001)
        assert reads['static'] == 1
        assert reads['live'] >= 2
        assert exported._task.done()
    finally:
        await exported.stop()
        await live.stop()


@pytest.mark.asyncio
async def test_slow_consumer_persists_entire_export_without_queue_loss(tmp_path):
    source = ExportSource(300)
    async def slow_publish(_):
        await asyncio.sleep(0.001)
    bus = SimpleNamespace(publish=AsyncMock(side_effect=slow_publish))
    owner = SessionOrchestrator(None, bus, [])
    writer = RawEventWriter(tmp_path / "raw.jsonl")
    await writer.open()
    await source.start()
    await asyncio.sleep(0.02)
    assert source.emitted == 2  # Producer waits instead of discarding rows.
    await owner._pump(source, "batch-test", writer)
    await source.stop()
    await writer.close()
    rows = [json.loads(line) for line in writer.path.read_text().splitlines()]
    assert [row["source_event_id"] for row in rows] == list(range(300))
    assert writer.count == bus.publish.await_count == 300
    assert source.dropped == 0 and source.read_complete.is_set()


@pytest.mark.asyncio
async def test_stop_drains_inflight_producer_before_iterator_finishes():
    source = ExportSource(2, capacity=1)
    await source.start()
    await asyncio.sleep(0.01)
    stop = asyncio.create_task(source.stop())
    await asyncio.sleep(0)
    rows = [row async for row in source.iter_events()]
    await stop
    assert [row.source_event_id for row in rows] == [0, 1]


@pytest.mark.asyncio
async def test_partial_batch_write_rolls_back_and_can_be_retried(tmp_path):
    class PartialFile(StringIO):
        failing = True
        calls = 0
        def write(self, value):
            self.calls += 1
            if self.failing and self.calls == 2:
                super().write(value[:10])
                raise OSError("simulated partial write")
            return super().write(value)
    writer = RawEventWriter(tmp_path / "unused")
    writer._file = PartialFile()
    bus = SimpleNamespace(publish=AsyncMock())
    owner = SessionOrchestrator(None, bus, [])
    rows = [event(0), event(1), event(2)]
    with pytest.raises(OSError, match="partial write"):
        await owner.ingest_raw_batch(rows, writer, channel="procmon")
    assert writer.count == 0 and writer._file.getvalue() == ""
    bus.publish.assert_not_awaited()
    writer._file.failing = False
    assert await owner.ingest_raw_batch(rows, writer, channel="procmon") == 3
    assert await owner.ingest_raw_batch(rows, writer, channel="procmon") == 0
    assert writer.count == bus.publish.await_count == 3
    assert len(writer._file.getvalue().splitlines()) == 3


@pytest.mark.asyncio
async def test_cancelled_batch_completes_once_before_overlapping_retry():
    entered, release = asyncio.Event(), asyncio.Event()
    async def write(_):
        entered.set()
        await release.wait()
    writer = SimpleNamespace(write=AsyncMock(), write_batch=AsyncMock(side_effect=write))
    bus = SimpleNamespace(publish=AsyncMock())
    owner = SessionOrchestrator(None, bus, [])
    rows = [event(0), event(1), event(0)]
    first = asyncio.create_task(owner.ingest_raw_batch(rows, writer))
    await entered.wait()
    first.cancel()
    retry = asyncio.create_task(owner.ingest_raw_batch(rows, writer))
    await asyncio.sleep(0)
    assert not first.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await retry == 0
    assert writer.write_batch.await_count == 1 and bus.publish.await_count == 2


@pytest.mark.asyncio
async def test_large_input_is_split_into_bounded_durable_batches(tmp_path):
    writer = RawEventWriter(tmp_path / "raw.jsonl")
    await writer.open()
    real_write = writer.write_batch
    sizes = []
    async def record_batch(rows):
        sizes.append(len(rows))
        await real_write(rows)
    writer.write_batch = record_batch
    owner = SessionOrchestrator(None, SimpleNamespace(publish=AsyncMock()), [])
    assert await owner.ingest_raw_batch([event(n) for n in range(300)], writer) == 300
    await writer.close()
    assert sizes == [128, 128, 44] and writer.count == 300


@pytest.mark.asyncio
async def test_incomplete_export_has_a_source_specific_bounded_failure(tmp_path, monkeypatch):
    import adam.orchestrator.session as module
    async def empty():
        if False:
            yield
    collector = SimpleNamespace(source_name="procmon", start=AsyncMock(), stop=AsyncMock(),
                                iter_events=empty, read_complete=asyncio.Event(), dropped=0, errors=[])
    monkeypatch.setattr(module, "build_collectors_from_telemetry", lambda *args, **kwargs: [collector])
    artifact = tmp_path / "capture.csv"
    artifact.write_text("retained partial capture")
    guest = SimpleNamespace(verify_tools=AsyncMock(return_value=ToolAvailability(True, True, True, {})),
        start_captures=AsyncMock(return_value={"procmon": True, "network": True}),
        stop_export_and_fetch=AsyncMock(return_value=TelemetryArtifacts(procmon_csv_path=str(artifact))))
    controller = SimpleNamespace(prepare=AsyncMock(), arm=AsyncMock(), detonate=AsyncMock(),
                                 teardown=AsyncMock(), vm_profile="mock", last_detonation_result=None)
    settings = Settings(sandbox={"vm_name": "mock", "guest_username": "mock", "guest_password": "mock"},
                        guest_backend="vbox")
    owner = SessionOrchestrator(controller, EventBus(), [], guest_agent=guest, live_poll_enabled=False,
                                artifacts_dir=tmp_path, post_detonation_drain_seconds=0,
                                export_ingest_timeout_s=0.01)
    sample = SampleRef(sha256="0"*64, md5="0"*32, filename="test.exe", size_bytes=1, file_type="exe")
    result = await owner.run_session(sample, settings, host_sample_path="unused")
    assert result.status == SessionStatus.FAILED
    assert any(f.stage == "procmon" and "0.01 seconds" in f.description for f in result.failures)
    assert artifact.read_text() == "retained partial capture"
    controller.teardown.assert_awaited_once()
