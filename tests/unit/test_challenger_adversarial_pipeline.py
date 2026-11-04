"""
tests/unit/test_challenger_adversarial_pipeline.py

Adversarial stress-testing suite for ADAM Host Pipeline & Telemetry Isolation.
Authored by Challenger 1 (EMPIRICAL CHALLENGER).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from adam.common.bus import EventBus
from adam.common.errors import ContractViolationError
from adam.contracts.enums import Category, Source
from adam.contracts.raw_event import RawEvent
from adam.contracts.session import SampleRef
from adam.orchestrator.persistence import RawEventWriter
from adam.orchestrator.session import SessionOrchestrator
from adam.sandbox.controller import SandboxController
from adam.sandbox.guest.http_channel import HTTPGuestChannel, SysmonEventsResult
from adam.sandbox.guest.http_models import (
    GuestAgentError,
    GuestAgentUnreachableError,
)
from adam.sandbox.state import SandboxState
from adam.sandbox.vbox.client import VirtualBoxClient
from adam.sandbox.vbox.models import VMOperationResult


def _envelope(data: object, success: bool = True, error_code: str | None = None, error_message: str | None = None) -> httpx.Response:
    payload = {
        "success": success,
        "error_code": error_code,
        "error_message": error_message,
        "data": data,
    }
    return httpx.Response(200, json=payload)


# ============================================================================ #
# 1. Process Query Error Behavior
# ============================================================================ #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport_exc",
    [
        httpx.ConnectError("Connection refused"),
        httpx.ConnectTimeout("Connect timed out"),
        httpx.ReadTimeout("Read timed out"),
        httpx.RemoteProtocolError("Remote protocol error"),
    ],
)
async def test_process_query_transport_failures_raise_unreachable_never_empty(transport_exc: Exception) -> None:
    def _transport_handler(request: httpx.Request) -> httpx.Response:
        raise transport_exc

    transport = httpx.MockTransport(_transport_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    with pytest.raises(GuestAgentUnreachableError) as exc_info:
        await ch.query_processes(pid=444)
    assert "could not reach guest agent" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected_err_pattern"),
    [
        (500, json.dumps({"success": False, "error_code": "INTERNAL_SERVER_ERROR", "error_message": "Script crashed"}), "INTERNAL_SERVER_ERROR"),
        (500, "<html><body>500 Internal Server Error</body></html>", "HTTP_500"),
        (502, "Bad Gateway", "HTTP_502"),
        (503, "Service Unavailable", "HTTP_503"),
        (401, json.dumps({"success": False, "error_code": "UNAUTHORIZED", "error_message": "Invalid token"}), "UNAUTHORIZED"),
        (403, "Forbidden", "ACCESS_DENIED"),
        (404, json.dumps({"success": False, "error_code": "NOT_FOUND", "error_message": "Endpoint not found"}), "NOT_FOUND"),
    ],
)
async def test_process_query_http_error_codes_raise_guest_agent_error(
    status_code: int, body: str, expected_err_pattern: str
) -> None:
    def _http_err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body.encode("utf-8"))

    transport = httpx.MockTransport(_http_err_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    with pytest.raises(GuestAgentError) as exc_info:
        await ch.query_processes(pid=444)
    assert expected_err_pattern in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt_response",
    [
        b"NOT_A_JSON_STRING",
        b"{malformed json: true",
        json.dumps({"success": True}).encode("utf-8"),  # missing data completely
        json.dumps({"success": True, "data": None}).encode("utf-8"),  # data is None (missing processes)
        json.dumps({"success": True, "data": {"processes": "not_a_list"}}).encode("utf-8"),
        json.dumps({"success": True, "data": {"processes": [{"pid": "not_an_int", "name": "bad"}]}}).encode("utf-8"),
        json.dumps({"unknown_envelope": 123}).encode("utf-8"),
    ],
)
async def test_process_query_malformed_payloads_raise_contract_violation(corrupt_response: bytes) -> None:
    def _corrupt_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=corrupt_response)

    transport = httpx.MockTransport(_corrupt_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    with pytest.raises(ContractViolationError):
        await ch.query_processes(pid=444)


@pytest.mark.asyncio
async def test_process_query_clean_empty_returns_empty_list() -> None:
    def _ok_empty_handler(request: httpx.Request) -> httpx.Response:
        return _envelope({"processes": []})

    transport = httpx.MockTransport(_ok_empty_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )
    result = await ch.query_processes(pid=444)
    assert result == []


# ============================================================================ #
# 2. Process Exit Detection & Controller Wait Loop
# ============================================================================ #


@pytest.mark.asyncio
async def test_controller_detonate_loop_resilient_to_query_errors(tmp_path: Path) -> None:
    """
    Stress-test SandboxController.detonate() polling:
    Simulate a sequence of query responses:
      Poll 1: [ProcessInfo(pid=5000, ...)]
      Poll 2: Transport error (ConnectError -> GuestAgentUnreachableError)
      Poll 3: HTTP 500 (GuestAgentError)
      Poll 4: Corrupt JSON (ContractViolationError)
      Poll 5: [ProcessInfo(pid=5000, ...)]
      Poll 6: [] (Process truly exited)
    Verify detonate loop survives polls 2-4 and only terminates at Poll 6.
    """
    sample_file = tmp_path / "target.exe"
    sample_file.write_bytes(b"MZ_DUMMY_BINARY")

    call_count = 0

    def _stateful_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if request.url.path == "/health":
            return _envelope({"status": "ok", "uptime_s": 10.0})
        if request.url.path == "/filesystem/exists":
            return _envelope({"exists": True, "is_directory": False, "size_bytes": 100})
        if request.url.path == "/sysmon/diagnostics":
            return _envelope({"channel_available": True, "event_count": 10})
        if request.url.path == "/sample/stage":
            body = json.loads(request.content.decode("utf-8"))
            return _envelope({"target_path": "C:\\ADAM\\target.exe", "sha256": body.get("sha256"), "size_bytes": 15})
        if request.url.path == "/process/start":
            return _envelope({"pid": 5000, "exit_code": None, "stdout": "", "stderr": ""})
        if request.url.path == "/process/query":
            call_count += 1
            if call_count == 1:
                return _envelope({"processes": [{"pid": 5000, "name": "target.exe", "command_line": "target.exe", "session_id": 1}]})
            elif call_count == 2:
                raise httpx.ConnectError("flapping network", request=request)
            elif call_count == 3:
                return httpx.Response(500, content=b"Internal Error")
            elif call_count == 4:
                return httpx.Response(200, content=b"BROKEN_JSON")
            elif call_count == 5:
                return _envelope({"processes": [{"pid": 5000, "name": "target.exe", "command_line": "target.exe", "session_id": 1}]})
            else:
                return _envelope({"processes": []})
        return _envelope({"status": "ok"})

    transport = httpx.MockTransport(_stateful_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    channel = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    mock_vbox = MagicMock(spec=VirtualBoxClient)
    mock_vbox.get_state = AsyncMock(return_value="poweroff")
    mock_vbox.restore_snapshot = AsyncMock(return_value=VMOperationResult(True, ("restore",), 1.0, 0, "", ""))
    mock_vbox.start = AsyncMock(return_value=VMOperationResult(True, ("start",), 1.0, 0, "", ""))
    mock_vbox.wait_for_state = AsyncMock(return_value=VMOperationResult(True, ("wait",), 1.0, 0, "", ""))
    mock_vbox.stop = AsyncMock(return_value=VMOperationResult(True, ("stop",), 1.0, 0, "", ""))

    ctrl = SandboxController(
        mock_vbox,
        "TestVM",
        guest_username="Adam",
        guest_password="Password",
        guest_channel=channel,
        detonate_timeout=10.0,
    )

    await ctrl.prepare()
    await ctrl.arm(str(sample_file), "C:\\ADAM\\target.exe")
    assert ctrl.state == SandboxState.ARMED

    sample_ref = SampleRef(
        sha256="0" * 64,
        md5="0" * 32,
        filename="target.exe",
        size_bytes=15,
        file_type="PE32",
    )

    await ctrl.detonate(sample_ref)
    assert ctrl.state == SandboxState.COMPLETED
    assert call_count >= 6  # Verified all intermediate failure states were handled without early exit


# ============================================================================ #
# 3. Unknown Exit Code Preservation
# ============================================================================ #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guest_exit_code", "expected_return_code", "expected_success", "expected_term_reason"),
    [
        (None, None, False, None),
        (0, 0, True, None),
        (1, 1, False, None),
        (-1, -1, False, None),
        (3221225477, 3221225477, False, "STATUS_ACCESS_VIOLATION"),
    ],
)
async def test_run_process_exit_code_preservation(
    guest_exit_code: int | None,
    expected_return_code: int | None,
    expected_success: bool,
    expected_term_reason: str | None,
) -> None:
    def _start_handler(request: httpx.Request) -> httpx.Response:
        return _envelope({
            "pid": 8888,
            "exit_code": guest_exit_code,
            "stdout": "sample output",
            "stderr": "sample error",
        })

    transport = httpx.MockTransport(_start_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    res = await ch.run_process("C:\\dummy.exe", wait=False)
    assert res.return_code == expected_return_code
    assert res.success == expected_success
    if expected_term_reason is not None:
        assert res.termination_reason == expected_term_reason
    else:
        assert res.termination_reason is None


# ============================================================================ #
# 4. Sysmon Watermark Filtering
# ============================================================================ #


@pytest.mark.asyncio
async def test_sysmon_watermark_boundary_filtering() -> None:
    from adam.collectors.sysmon import SysmonCollector

    watermark = 50
    collector = SysmonCollector("dummy.evtx", session_id="sess_watermark_stress", watermark_record_id=watermark)

    record_ids = [-5, 0, 1, 49, 50, 51, 52, 100]
    records = [
        f"""<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
            <System>
                <EventID>1</EventID>
                <EventRecordID>{rid}</EventRecordID>
                <TimeCreated SystemTime="2026-01-01T00:00:00.000000000Z"/>
            </System>
        </Event>"""
        for rid in record_ids
    ]
    # Add one malformed XML record that should be skipped without stopping collection
    records.insert(3, "<Event>malformed XML snippet</Event>")

    collector._read_current_records = lambda: records  # type: ignore[assignment]
    await collector.start()
    await asyncio.sleep(0.05)
    await collector.stop()

    emitted: list[RawEvent] = []
    async for ev in collector.iter_events():
        emitted.append(ev)

    emitted_ids = [ev.source_event_id for ev in emitted]
    # Invariant: Every emitted record must have EventRecordID > watermark
    assert emitted_ids == [51, 52, 100]
    assert all(eid > watermark for eid in emitted_ids)
    assert 50 not in emitted_ids
    assert 49 not in emitted_ids


# ============================================================================ #
# 5. Single Ingestion & Deduplication
# ============================================================================ #


@pytest.mark.asyncio
async def test_single_ingestion_and_deduplication_adversarial(tmp_path: Path) -> None:
    bus = MagicMock(spec=EventBus)
    bus.publish = AsyncMock()

    raw_file = tmp_path / "raw.jsonl"
    writer = RawEventWriter(raw_file)
    await writer.open()

    ctrl = MagicMock()
    orchestrator = SessionOrchestrator(ctrl, bus, collectors=[])
    orchestrator._session_id = "sess_stress"

    now = datetime.now(timezone.utc)

    # 1. Live event: sysmon, record_id=777, gen=0
    live_event = RawEvent(
        event_id="live_uuid_1",
        session_id="sess_stress",
        source=Source.SYSMON,
        source_event_id=777,
        category=Category.PROCESS,
        occurred_at=now,
        observed_at=now,
    )
    assert await orchestrator.ingest_raw_event(live_event, writer, channel="sysmon", generation=0) is True

    # 2. Duplicate offline event: sysmon, record_id=777, gen=0 (different event_id)
    offline_event = RawEvent(
        event_id="offline_uuid_2",
        session_id="sess_stress",
        source=Source.SYSMON,
        source_event_id=777,
        category=Category.PROCESS,
        occurred_at=now,
        observed_at=now,
    )
    assert await orchestrator.ingest_raw_event(offline_event, writer, channel="sysmon", generation=0) is False

    # 3. Collision avoidance: procmon event with same record_id=777 must be accepted
    procmon_event = RawEvent(
        event_id="procmon_uuid_3",
        session_id="sess_stress",
        source=Source.PROCMON,
        source_event_id=777,
        category=Category.PROCESS,
        occurred_at=now,
        observed_at=now,
    )
    assert await orchestrator.ingest_raw_event(procmon_event, writer, channel="procmon", generation=0) is True

    # 4. Log reset event: sysmon event with same record_id=777 but gen=1 must be accepted
    reset_event = RawEvent(
        event_id="reset_uuid_4",
        session_id="sess_stress",
        source=Source.SYSMON,
        source_event_id=777,
        category=Category.PROCESS,
        occurred_at=now,
        observed_at=now,
    )
    assert await orchestrator.ingest_raw_event(reset_event, writer, channel="sysmon", generation=1) is True

    # 5. Session mismatch check
    foreign_event = RawEvent(
        event_id="foreign_uuid_5",
        session_id="sess_FOREIGN",
        source=Source.SYSMON,
        source_event_id=888,
        category=Category.PROCESS,
        occurred_at=now,
        observed_at=now,
    )
    with pytest.raises(ValueError, match="session ownership"):
        await orchestrator.ingest_raw_event(foreign_event, writer, channel="sysmon", generation=0)

    await writer.close()

    lines = raw_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3  # live_event, procmon_event, reset_event
    assert bus.publish.call_count == 3


# ============================================================================ #
# 6. Multi-page Sysmon Draining & Log Reset Detection
# ============================================================================ #


@pytest.mark.asyncio
async def test_multipage_sysmon_events_draining_and_reset() -> None:
    calls = []

    def _paginated_handler(request: httpx.Request) -> httpx.Response:
        rid = int(request.url.params.get("record_id", 0))
        calls.append(rid)
        if len(calls) == 1:
            # Page 1: 100 events (1 to 100)
            events = [{"record_id": i, "event_id": 1, "time_created": "2026-01-01T00:00:00Z", "data": {}} for i in range(1, 101)]
            return _envelope({"last_record_id": 100, "events": events, "log_reset": False})
        elif len(calls) == 2:
            # Page 2: 100 events (101 to 200)
            events = [{"record_id": i, "event_id": 1, "time_created": "2026-01-01T00:00:00Z", "data": {}} for i in range(101, 201)]
            return _envelope({"last_record_id": 200, "events": events, "log_reset": False})
        elif len(calls) == 3:
            # Page 3: 40 events (201 to 240) with log_reset=True
            events = [{"record_id": i, "event_id": 1, "time_created": "2026-01-01T00:00:00Z", "data": {}} for i in range(201, 241)]
            return _envelope({"last_record_id": 240, "events": events, "log_reset": True})
        else:
            return _envelope({"last_record_id": 240, "events": [], "log_reset": False})

    transport = httpx.MockTransport(_paginated_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    result = await ch.get_sysmon_events_since(record_id=0)
    assert isinstance(result, SysmonEventsResult)
    last_id, events = result
    assert last_id == 240
    # Do not assign already-read old-generation records to the reset log.
    assert len(events) == 40
    assert all(item.record_id >= 201 for item in events)
    assert result.log_reset is True
    assert calls == [0, 100, 200]


@pytest.mark.asyncio
async def test_multipage_sysmon_events_loop_prevention_on_stagnant_id() -> None:
    calls = []

    def _stagnant_handler(request: httpx.Request) -> httpx.Response:
        rid = int(request.url.params.get("record_id", 0))
        calls.append(rid)
        # 100 events returned, but last_record_id does not advance beyond requested record_id
        events = [{"record_id": rid, "event_id": 1, "time_created": "2026-01-01T00:00:00Z", "data": {}} for _ in range(100)]
        return _envelope({"last_record_id": rid, "events": events, "log_reset": False})

    transport = httpx.MockTransport(_stagnant_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    result = await ch.get_sysmon_events_since(record_id=50)
    last_id, events = result
    assert last_id == 50
    assert len(events) == 100
    assert len(calls) == 1  # No infinite loop; terminates immediately


@pytest.mark.asyncio
async def test_multipage_sysmon_events_midway_transport_failure() -> None:
    calls = []

    def _flaky_handler(request: httpx.Request) -> httpx.Response:
        rid = int(request.url.params.get("record_id", 0))
        calls.append(rid)
        if len(calls) == 1:
            events = [{"record_id": i, "event_id": 1, "time_created": "2026-01-01T00:00:00Z", "data": {}} for i in range(1, 101)]
            return _envelope({"last_record_id": 100, "events": events, "log_reset": False})
        else:
            raise httpx.ConnectError("guest crashed on page 2", request=request)

    transport = httpx.MockTransport(_flaky_handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake-guest:8765")
    ch = HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM",
        procmon_path="p",
        tshark_path="t",
        sysmon_log="Sysmon",
        client=client,
        retry_attempts=1,
    )

    # A partial page must not disguise a failed source as a successful poll.
    with pytest.raises(RuntimeError, match="Sysmon polling failed"):
        await ch.get_sysmon_events_since(record_id=0)


@pytest.mark.asyncio
async def test_concurrent_ingest_deduplication(tmp_path: Path) -> None:
    bus = MagicMock(spec=EventBus)
    bus.publish = AsyncMock()

    raw_file = tmp_path / "raw_concurrent.jsonl"
    writer = RawEventWriter(raw_file)
    await writer.open()

    ctrl = MagicMock()
    orchestrator = SessionOrchestrator(ctrl, bus, collectors=[])
    orchestrator._session_id = "sess_concurrent"

    now = datetime.now(timezone.utc)

    # Concurrently ingest 50 identical events across multiple tasks
    async def _attempt_ingest(task_id: int) -> bool:
        ev = RawEvent(
            event_id=f"event_{task_id}",
            session_id="sess_concurrent",
            source=Source.SYSMON,
            source_event_id=9999,
            category=Category.PROCESS,
            occurred_at=now,
            observed_at=now,
        )
        return await orchestrator.ingest_raw_event(ev, writer, channel="sysmon", generation=0)

    results = await asyncio.gather(*[_attempt_ingest(i) for i in range(50)])
    assert results.count(True) == 1
    assert results.count(False) == 49

    await writer.close()
    lines = raw_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert bus.publish.call_count == 1

