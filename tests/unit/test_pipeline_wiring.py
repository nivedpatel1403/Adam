"""Phase 3 tests for the live Fusion -> Policy -> Deception wiring."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from adam.common.bus import EventBus
from adam.contracts.enums import Category, MutationStatus, Source, Verdict
from adam.contracts.envelope import Envelope
from adam.contracts.mutation import MutationResult
from adam.contracts.policy_decision import PolicyDecision
from adam.contracts.raw_event import ProcessInfo, RawEvent
from adam.contracts.semantic_event import SemanticEvent
from adam.fusion.models import (
    FusionResult,
    RawEvent as FusionRawEvent,
    SemanticEvent as FusionSemanticEvent,
)
from adam.pipeline.wiring import wire_engines


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _raw_event(*, attributes: dict | None = None) -> RawEvent:
    return RawEvent(
        event_id=f"raw_{uuid.uuid4().hex[:8]}",
        session_id="sess_wiring",
        source=Source.SYSMON,
        source_event_id=1,
        category=Category.PROCESS,
        occurred_at=_now(),
        observed_at=_now(),
        process=ProcessInfo(
            pid=123,
            ppid=1,
            image="C:\\Temp\\evil.exe",
            command_line="evil.exe /run",
            integrity_level="Medium",
            user="CORP\\user",
            guid="{real-guid}",
        ),
        attributes=attributes
        or {
            "Image": "C:\\Temp\\evil.exe",
            "ProcessGuid": "{real-guid}",
            "command_line": "evil.exe /run",
        },
    )


def _fusion_event(payload: dict, *, process_name: str = "evil.exe") -> FusionRawEvent:
    return FusionRawEvent(
        timestamp=_now(),
        source="sysmon",
        event_type="PROCESS",
        process_id=123,
        process_name=process_name,
        command_line=str(payload.get("command_line", "-")),
        payload=payload,
    )


def _detection(
    *,
    technique: str = "T1082",
    confidence: float = 0.55,
    evidence: list[FusionRawEvent] | None = None,
) -> FusionSemanticEvent:
    return FusionSemanticEvent(
        timestamp=_now(),
        category="Test",
        technique_id=technique,
        severity="LOW",
        confidence=confidence,
        description="test detection",
        evidence=evidence
        or [
            _fusion_event(
                {
                    "Image": "C:\\Temp\\evil.exe",
                    "ProcessGuid": "{real-guid}",
                }
            )
        ],
    )


class FakeFusion:
    def __init__(self, detection: FusionSemanticEvent) -> None:
        self.detection = detection

    def process(self, events) -> FusionResult:
        return FusionResult(
            timestamp=_now(),
            processed_events=len(events),
            normalized_events=len(events),
            correlated_groups=1,
            detections=[self.detection],
        )


class FakePolicy:
    def evaluate(self, event, context):
        return []


class FakeDeception:
    def __init__(self, *, applied_at: datetime | None = None) -> None:
        self.applied_at = applied_at or _now()
        self.calls = 0

    async def execute_async(self, decision: PolicyDecision) -> MutationResult:
        self.calls += 1
        return MutationResult(
            mutation_id="mut_wiring",
            session_id=decision.session_id,
            correlation_id=decision.correlation_id,
            decision_id=decision.decision_id,
            primitive="test",
            status=MutationStatus.APPLIED,
            applied_at=self.applied_at,
        )


def _raw_envelope(raw: RawEvent) -> Envelope[RawEvent]:
    return Envelope[RawEvent](
        message_id=str(uuid.uuid4()),
        message_type="RawEvent",
        session_id=raw.session_id,
        correlation_id=raw.event_id,
        emitted_at=_now(),
        emitter="test",
        payload=raw,
    )


async def _publish_raw(bus: EventBus, raw: RawEvent) -> list[SemanticEvent]:
    received: list[SemanticEvent] = []

    async def handler(env: Envelope[SemanticEvent]) -> None:
        received.append(env.payload)

    sub = bus.subscribe(SemanticEvent, handler, name="test_semantic_capture")
    await bus.start()
    await bus.publish(_raw_envelope(raw))
    await bus.drain(1.0)
    sub.unsubscribe()
    return received


@pytest.mark.asyncio
async def test_wiring_does_not_inflate_confidence() -> None:
    bus = EventBus()
    wire_engines(
        bus,
        fusion=FakeFusion(_detection(confidence=0.55)),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )
    received = await _publish_raw(bus, _raw_event())
    assert len(received) == 1
    assert received[0].confidence == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_wiring_uses_real_actor_image_and_guid() -> None:
    bus = EventBus()
    wire_engines(
        bus,
        fusion=FakeFusion(_detection()),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )
    received = await _publish_raw(bus, _raw_event())
    assert received[0].actor.image == "C:\\Temp\\evil.exe"
    assert received[0].actor.guid == "{real-guid}"


@pytest.mark.asyncio
async def test_distinct_registry_keys_counts_evidence() -> None:
    evidence = [
        _fusion_event({"TargetObject": rf"HKCU\Software\Run\value{i}"}, process_name="reg.exe")
        for i in range(3)
    ]
    bus = EventBus()
    wire_engines(
        bus,
        fusion=FakeFusion(_detection(technique="T1547", confidence=0.88, evidence=evidence)),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )
    received = await _publish_raw(bus, _raw_event())
    assert received[0].features["distinct_registry_keys"] == 3


@pytest.mark.asyncio
async def test_wiring_sets_network_endpoint_from_dns() -> None:
    evidence = [
        _fusion_event(
            {"Image": "C:\\Temp\\malware.exe", "QueryName": "c2.baddomain.org"},
            process_name="malware.exe",
        )
    ]
    bus = EventBus()
    wire_engines(
        bus,
        fusion=FakeFusion(_detection(technique="T1071", confidence=0.91, evidence=evidence)),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )
    received = await _publish_raw(bus, _raw_event())
    assert received[0].features["network_endpoint"] == "c2.baddomain.org"


@pytest.mark.asyncio
async def test_wiring_uses_synthetic_dest_ip_for_network_endpoint() -> None:
    evidence = [
        _fusion_event(
            {"dest_ip": "185.220.101.7", "domain": "update-service.net"},
            process_name="curl.exe",
        )
    ]
    bus = EventBus()
    wire_engines(
        bus,
        fusion=FakeFusion(_detection(technique="T1071", confidence=0.91, evidence=evidence)),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )
    received = await _publish_raw(bus, _raw_event())
    assert received[0].features["network_endpoint"] == "185.220.101.7"


@pytest.mark.asyncio
async def test_absent_ldap_evidence_does_not_set_ldap_features() -> None:
    bus = EventBus()
    wire_engines(
        bus,
        fusion=FakeFusion(_detection(technique="T1082", confidence=0.80)),
        policy=FakePolicy(),
        deception=FakeDeception(),
    )
    received = await _publish_raw(bus, _raw_event())
    assert "ldap_attempts" not in received[0].features
    assert "all_failed" not in received[0].features


def _decision(session_id: str = "sess_wiring") -> PolicyDecision:
    return PolicyDecision(
        decision_id="dec_wiring",
        session_id=session_id,
        correlation_id="corr_wiring",
        triggered_by="sem_wiring",
        rule_id="RULE-TEST",
        rule_version="1.0.0",
        action="FABRICATE_C2_RESPONSE",
        verdict=Verdict.EXECUTE,
        priority=50,
        parameters={"target_url": "http://203.0.113.9/beacon"},
        rationale="test",
    )


def _decision_envelope(decision: PolicyDecision) -> Envelope[PolicyDecision]:
    return Envelope[PolicyDecision](
        message_id=str(uuid.uuid4()),
        message_type="PolicyDecision",
        session_id=decision.session_id,
        correlation_id=decision.correlation_id,
        emitted_at=_now(),
        emitter="test",
        payload=decision,
    )


async def _wire_with_deception(*, applied_at: datetime, causal_window_ms: int):
    bus = EventBus()
    deception = FakeDeception(applied_at=applied_at)
    handles = wire_engines(
        bus,
        fusion=FakeFusion(_detection(confidence=0.80)),
        policy=FakePolicy(),
        deception=deception,
        resolve_dry_run=lambda _session_id: False,
        causal_window_ms=causal_window_ms,
    )
    await bus.start()
    return bus, deception, handles


@pytest.mark.asyncio
async def test_caused_by_mutation_within_window() -> None:
    bus, deception, handles = await _wire_with_deception(
        applied_at=_now(), causal_window_ms=5_000
    )
    await bus.publish(_decision_envelope(_decision()))
    await bus.drain(1.0)
    assert deception.calls == 1

    received = await _publish_raw(bus, _raw_event())
    assert received[0].caused_by_mutation == "mut_wiring"
    assert handles.mutations_applied == 1
    assert handles.semantic_events_post_mutation == 1


@pytest.mark.asyncio
async def test_outside_window_is_none() -> None:
    bus, _, handles = await _wire_with_deception(
        applied_at=_now() - timedelta(seconds=10), causal_window_ms=1_000
    )
    await bus.publish(_decision_envelope(_decision()))
    await bus.drain(1.0)

    received = await _publish_raw(bus, _raw_event())
    assert received[0].caused_by_mutation is None
    assert handles.semantic_events_post_mutation == 0


@pytest.mark.asyncio
async def test_session_metrics_populated() -> None:
    bus, _, handles = await _wire_with_deception(
        applied_at=_now(), causal_window_ms=5_000
    )
    await bus.publish(_decision_envelope(_decision()))
    await bus.drain(1.0)
    await _publish_raw(bus, _raw_event())

    assert handles.delivered("fusion_raw") == 1
    assert handles.delivered("policy_semantic") == 1
    assert handles.decisions_total == 0
    assert handles.mutations_applied == 1
    assert handles.semantic_events_post_mutation == 1
