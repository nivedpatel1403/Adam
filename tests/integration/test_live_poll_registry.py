"""Integration: a live registry event reaches Fusion/Policy during polling.

This is the Phase 3 acceptance path that previously only worked after the
post-run EVTX export: a Sysmon RegistryEvent (ID 13) is polled during
detonation, converted to RawEvent, fused, and published as a PERSIST_RUN_KEY
SemanticEvent before the session ends.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from adam.common.bus import EventBus
from adam.contracts.envelope import Envelope
from adam.contracts.semantic_event import SemanticEvent
from adam.fusion.engine import EventFusionEngine
from adam.orchestrator.session import SessionOrchestrator
from adam.pipeline.wiring import wire_engines
from adam.sandbox.guest.http_models import SysmonRecordItem


class FakeController:
    vm_profile = None


class FakePolicy:
    def evaluate(self, event, context):
        return []


class FakeDeception:
    async def execute_async(self, decision):
        raise AssertionError("no decision should be produced by this test")


class FakeSysmonChannel:
    def __init__(self, record: SysmonRecordItem) -> None:
        self.record = record
        self.sent = False
        self.stop_event: asyncio.Event | None = None

    async def get_sysmon_events_since(
        self,
        record_id: int = 0,
        event_ids: str = "1,3",
        channel: str | None = None,
    ) -> tuple[int, list[SysmonRecordItem]]:
        if record_id < 0:
            return 500, []
        if not self.sent:
            self.sent = True
            return self.record.record_id, [self.record]
        if self.stop_event is not None:
            self.stop_event.set()
        return record_id, []


@pytest.mark.asyncio
async def test_live_registry_event_reaches_fusion_during_poll(tmp_path: Path) -> None:
    record = SysmonRecordItem(
        record_id=501,
        event_id=13,
        time_created="2026-01-01T00:00:00.000Z",
        data={
            "Image": "C:\\Windows\\System32\\reg.exe",
            "ProcessId": "123",
            "ProcessGuid": "{live-guid}",
            "User": "CORP\\user",
            "EventType": "SetValue",
            "TargetObject": r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\evil",
            "Details": "C:\\Temp\\evil.exe",
        },
    )
    channel = FakeSysmonChannel(record)
    bus = EventBus()
    wire_engines(
        bus,
        fusion=EventFusionEngine(),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )

    received: list[SemanticEvent] = []

    async def capture(env: Envelope[SemanticEvent]) -> None:
        received.append(env.payload)

    bus.subscribe(SemanticEvent, capture, name="test_capture")
    await bus.start()

    orchestrator = SessionOrchestrator(
        FakeController(),  # type: ignore[arg-type]
        bus,
        [],
        artifacts_dir=tmp_path,
        guest_agent=channel,  # type: ignore[arg-type]
    )
    stop = asyncio.Event()
    channel.stop_event = stop

    await asyncio.wait_for(
        orchestrator._run_live_sysmon_poll(
            "sess_live",
            stop,
            initial_record_id=-1,
            poll_interval_s=0.01,
        ),
        timeout=2.0,
    )
    await bus.drain(1.0)

    assert received, "live registry event produced no SemanticEvent"
    assert received[0].intent == "PERSIST_RUN_KEY"
    assert received[0].features["target_object"].endswith("Run\\evil")
    assert received[0].actor.image == "C:\\Windows\\System32\\reg.exe"
    assert received[0].actor.guid == "{live-guid}"
