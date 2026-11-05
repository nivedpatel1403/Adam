import asyncio
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adam.common.bus import EventBus
from adam.common.config import Settings
from adam.contracts.enums import Category, SessionStatus, Source
from adam.contracts.raw_event import RawEvent
from adam.contracts.session import SampleRef, SessionLifecycle
from adam.db.writer import DBWriter
from adam.orchestrator.persistence import RawEventWriter
from adam.orchestrator.session import SessionOrchestrator
from adam.sandbox.controller import SandboxController
from adam.sandbox.guest.agent.agent import ToolAvailability
from adam.sandbox.state import SandboxOperationError, SandboxState
from scripts import verify_candidate_snapshots as harness
from scripts.verify_candidate_snapshots import promote_candidate_to_clean


def event():
    now = datetime.now(UTC)
    return RawEvent(event_id="test", session_id="test", source=Source.SYSMON,
                    source_event_id=1, category=Category.PROCESS,
                    occurred_at=now, observed_at=now)


@pytest.mark.asyncio
async def test_failed_write_can_be_retried():
    bus = SimpleNamespace(publish=AsyncMock())
    owner = SessionOrchestrator(None, bus, [])
    writer = SimpleNamespace(write=AsyncMock(side_effect=[OSError("disk full"), None]))
    with pytest.raises(OSError):
        await owner.ingest_raw_event(event(), writer)
    assert await owner.ingest_raw_event(event(), writer)
    assert writer.write.await_count == 2
    assert bus.publish.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_writes_are_deduplicated():
    owner = SessionOrchestrator(None, SimpleNamespace(publish=AsyncMock()), [])
    writer = SimpleNamespace(write=AsyncMock())
    results = await asyncio.gather(*(owner.ingest_raw_event(event(), writer) for _ in range(20)))
    assert sum(results) == 1
    writer.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_deadline_is_failure_and_terminates_only_owned_pid():
    guest = SimpleNamespace(run_process=AsyncMock(return_value=SimpleNamespace(pid=123)),
                            query_processes=AsyncMock(return_value=[object()]),
                            terminate_process=AsyncMock())
    client = SimpleNamespace(stop=AsyncMock(), restore_snapshot=AsyncMock())
    ctrl = SandboxController(client, "test", guest_username="test", guest_password="test",
                             guest_channel=guest, detonate_timeout=0.001)
    ctrl._state = SandboxState.ARMED
    ctrl._armed_guest_target_path = "test.exe"
    with pytest.raises(SandboxOperationError, match="did not exit"):
        await ctrl.detonate(SimpleNamespace(filename="test.exe"))
    guest.terminate_process.assert_awaited_once_with(pid=123)
    assert ctrl.state == SandboxState.ERROR


@pytest.mark.asyncio
async def test_promotion_rejects_same_snapshot_without_mutation():
    client = SimpleNamespace(list_snapshots=AsyncMock(return_value=[SimpleNamespace(name="clean", uuid="same")]),
                             _run=AsyncMock())
    result = await promote_candidate_to_clean(client, "test", "clean", "same")
    assert not result["success"]
    client._run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_check", ["telemetry_verified", "vm_stopped", "pre_clean_verified", "guest_ready", "live_workload_observed"])
async def test_harness_never_promotes_incomplete_cycle(tmp_path, monkeypatch, failed_check):
    disk = tmp_path / "orphan.vdi"
    disk.write_bytes(b"unchanged disk")
    snapshots = [SimpleNamespace(name="candidate", uuid="candidate-id"),
                 SimpleNamespace(name="clean", uuid="recovery-id")]
    vbox = SimpleNamespace(list_snapshots=AsyncMock(return_value=snapshots))
    monkeypatch.setattr(harness, "VirtualBoxClient", lambda: vbox)
    settings = SimpleNamespace(sandbox=SimpleNamespace(vm_name="test"), http_guest=SimpleNamespace(
        base_url="http://unused", capture_dir="unused", procmon_path="unused", tshark_path="unused",
        sysmon_log="unused", tshark_interface="auto", auth_token=None))
    monkeypatch.setattr(harness, "get_settings", lambda: settings)
    monkeypatch.setattr(harness, "HTTPGuestChannel", lambda **kwargs: SimpleNamespace(aclose=AsyncMock()))
    checks = {key: True for key in ("restore_success", "guest_ready", "pre_clean_verified", "marker_planted", "workload_executed", "telemetry_verified", "vm_stopped", "state_reset_verified", "live_workload_observed")}
    checks[failed_check] = False
    monkeypatch.setattr(harness, "run_verification_cycle", AsyncMock(return_value=checks))
    promotion = AsyncMock()
    monkeypatch.setattr(harness, "promote_candidate_to_clean", promotion)
    args = SimpleNamespace(vdi_path=str(disk), output_file=str(tmp_path / "result.json"),
        vm_name="test", candidate_snapshot="candidate", recovery_snapshot="clean",
        guest_url="http://unused", promote=True)
    assert await harness._run_harness(args) == 1
    promotion.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_write_finishes_publication_before_retry():
    writing = asyncio.Event()
    release = asyncio.Event()
    async def write(_):
        writing.set()
        await release.wait()
    bus = SimpleNamespace(publish=AsyncMock())
    owner = SessionOrchestrator(None, bus, [])
    writer = SimpleNamespace(write=AsyncMock(side_effect=write))
    task = asyncio.create_task(owner.ingest_raw_event(event(), writer))
    await writing.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await owner.ingest_raw_event(event(), writer)
    writer.write.assert_awaited_once()
    bus.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_retry_does_not_write_the_event_twice():
    bus = SimpleNamespace(publish=AsyncMock(side_effect=[RuntimeError("bus unavailable"), None]))
    owner = SessionOrchestrator(None, bus, [])
    writer = SimpleNamespace(write=AsyncMock())
    with pytest.raises(RuntimeError):
        await owner.ingest_raw_event(event(), writer)
    assert await owner.ingest_raw_event(event(), writer)
    writer.write.assert_awaited_once()
    assert bus.publish.await_count == 2


def test_partial_disk_write_is_removed_before_retry(tmp_path):
    class PartialFile(StringIO):
        fail = True
        def write(self, value):
            if self.fail:
                self.fail = False
                super().write(value[:10])
                raise OSError("disk full")
            return super().write(value)
    writer = RawEventWriter(tmp_path / "raw.jsonl")
    writer._file = PartialFile()
    with pytest.raises(OSError):
        writer._write_sync(event())
    assert writer._file.getvalue() == ""
    writer._write_sync(event())
    assert writer.count == 1
    assert len(writer._file.getvalue().splitlines()) == 1


@pytest.mark.asyncio
async def test_database_shutdown_is_bounded_and_keeps_uncommitted_events():
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    writer = DBWriter(db, EventBus())
    writer.event_repo.create_raw = AsyncMock(side_effect=OSError("disk full"))
    writer._enqueue(SimpleNamespace(payload=event()))
    with pytest.raises(RuntimeError, match="three retries"):
        await writer.stop()
    assert len(writer.queue) == 1
    assert writer.event_repo.create_raw.await_count == 3


@pytest.mark.asyncio
async def test_bus_drain_waits_for_cascaded_inflight_handlers():
    bus = EventBus()
    received = []

    async def upstream(_):
        await asyncio.sleep(0.01)
        await bus.publish(SimpleNamespace(payload=1))

    async def middle(_):
        await asyncio.sleep(0.01)
        await bus.publish(SimpleNamespace(payload=1.0))

    async def downstream(_):
        await asyncio.sleep(0.01)
        received.append('complete')

    bus.subscribe(str, upstream, name='upstream')
    bus.subscribe(int, middle, name='middle')
    bus.subscribe(float, downstream, name='downstream')
    await bus.start()
    await bus.publish(SimpleNamespace(payload='start'))
    await bus.drain(timeout=1)
    assert received == ['complete']
    assert not bus.errors


@pytest.mark.asyncio
async def test_prepare_failure_delivers_queued_lifecycle_without_drain_failure(tmp_path):
    controller = SimpleNamespace(
        prepare=AsyncMock(side_effect=RuntimeError("guest never became ready")),
        teardown=AsyncMock(), vm_profile="mock", last_detonation_result=None,
    )
    bus = EventBus()
    statuses = []
    async def collect(envelope):
        statuses.append(envelope.payload.status)
    bus.subscribe(SessionLifecycle, collect, name="early_failure_lifecycle")
    settings = Settings(sandbox={"vm_name": "mock", "guest_username": "mock", "guest_password": "mock"})
    owner = SessionOrchestrator(controller, bus, [], artifacts_dir=tmp_path, bus_drain_timeout=0.05)
    sample = SampleRef(sha256="0"*64, md5="0"*32, filename="test.exe", size_bytes=1, file_type="exe")
    session = await owner.run_session(sample, settings, host_sample_path="unused")
    assert session.status == SessionStatus.FAILED
    assert statuses == [SessionStatus.PENDING, SessionStatus.PREPARING, SessionStatus.FAILED]
    assert [failure.stage for failure in session.failures] == ["execution"]
    assert not bus.errors
    controller.teardown.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_failure_is_the_final_published_status(tmp_path):
    controller = SimpleNamespace(prepare=AsyncMock(), arm=AsyncMock(), detonate=AsyncMock(),
        teardown=AsyncMock(side_effect=RuntimeError("cleanup failed")),
        vm_profile="mock", last_detonation_result=None)
    bus = EventBus()
    statuses = []
    async def collect(envelope):
        statuses.append(envelope.payload.status)
    bus.subscribe(SessionLifecycle, collect, name="test_lifecycle")
    settings = Settings(sandbox={"vm_name": "mock", "guest_username": "mock", "guest_password": "mock"})
    owner = SessionOrchestrator(controller, bus, [], artifacts_dir=tmp_path, post_detonation_drain_seconds=0)
    sample = SampleRef(sha256="0"*64, md5="0"*32, filename="test.exe", size_bytes=1, file_type="exe")
    session = await owner.run_session(sample, settings, host_sample_path="unused")
    assert session.status == SessionStatus.FAILED
    assert statuses[-1] == SessionStatus.FAILED
    assert SessionStatus.COMPLETED not in statuses
    assert any(f.stage == "teardown" for f in session.failures)


@pytest.mark.asyncio
async def test_strict_capture_rejects_present_tools_with_stopped_driver(tmp_path):
    controller = SimpleNamespace(prepare=AsyncMock(), arm=AsyncMock(), teardown=AsyncMock(),
        vm_profile="mock", last_detonation_result=None)
    guest = SimpleNamespace(verify_tools=AsyncMock(return_value=ToolAvailability(True, True, True, {})),
        get_tool_diagnostics=AsyncMock(return_value=SimpleNamespace(tools={
            "sysmon": {"available": True}, "procmon": {"available": True},
            "tshark": {"available": True}, "npcap": {"service_status": "Stopped"}})))
    settings = Settings(sandbox={"vm_name": "mock", "guest_username": "mock", "guest_password": "mock"},
        guest_backend="http", http_guest={"tools_required": True})
    owner = SessionOrchestrator(controller, EventBus(), [], artifacts_dir=tmp_path, guest_agent=guest)
    sample = SampleRef(sha256="0"*64, md5="0"*32, filename="test.exe", size_bytes=1, file_type="exe")
    session = await owner.run_session(sample, settings, host_sample_path="unused")
    assert session.status == SessionStatus.FAILED
    assert not session.source_outcomes["network"].verified
    controller.arm.assert_not_awaited()
