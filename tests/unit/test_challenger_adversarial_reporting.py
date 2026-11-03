"""
tests/unit/test_challenger_adversarial_reporting.py

Adversarial stress-testing suite for ADAM Deception, Metrics, Reporting, and Testing fixes.
Authored by Challenger 2 (EMPIRICAL CHALLENGER).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adam.api.main import sessions_store, store_event
from adam.common.bus import EventBus
from adam.contracts.enums import Arm, Category, MutationStatus, NetworkMode, SessionStatus, Source, Verdict
from adam.contracts.envelope import Envelope
from adam.contracts.mutation import MutationResult
from adam.contracts.policy_decision import PolicyDecision
from adam.contracts.raw_event import RawEvent
from adam.contracts.session import AnalysisSession, SampleRef, SessionConfig, SessionMetrics
from adam.deception.engine import DeceptionEngine
from adam.deception.primitives.network_lures import FabricateC2Response
from adam.orchestrator.runner import Runner
from adam.orchestrator.session import SessionOrchestrator
from adam.pipeline.wiring import wire_engines
from adam.reporting.generator import ReportGenerator
from scripts.verify_candidate_snapshots import check_orphan_vdi


# ============================================================================ #
# 1. Control Arm vs Treatment Arm Metric Isolation (Pipeline Wiring)
# ============================================================================ #


@pytest.mark.asyncio
async def test_control_arm_strict_metric_isolation() -> None:
    """Verify that a Control arm run (dry_run=True) reports 0 decisions_executed and 0 mutations."""
    bus = EventBus()
    fusion = MagicMock()
    policy = MagicMock()
    deception = MagicMock()

    mock_mutation = MutationResult(
        mutation_id="mut_ctrl_test",
        session_id="sess_ctrl",
        correlation_id="corr_ctrl",
        decision_id="dec_ctrl",
        primitive="test@1.0",
        status=MutationStatus.APPLIED,
        latency_ms=1.0,
        changes=[],
    )
    deception.execute_async = AsyncMock(return_value=mock_mutation)

    async def _resolve_dry_run(_sid: str) -> bool:
        return True  # Control arm

    handles = wire_engines(
        bus,
        fusion=fusion,
        policy=policy,
        deception=deception,
        resolve_dry_run=_resolve_dry_run,
    )
    await bus.start()

    decision = PolicyDecision(
        decision_id="dec_ctrl_1",
        session_id="sess_ctrl",
        correlation_id="corr_ctrl_1",
        rule_id="RULE-001",
        rule_version="1.0",
        triggered_by="sem_ctrl_1",
        verdict=Verdict.EXECUTE,
        action="TEST_ACTION",
        parameters={},
        rationale="testing control isolation",
    )
    env = Envelope[PolicyDecision](
        message_id=str(uuid.uuid4()),
        message_type="PolicyDecision",
        session_id="sess_ctrl",
        correlation_id="corr_ctrl_1",
        emitted_at=datetime.now(timezone.utc),
        emitter="PolicyEngine",
        payload=decision,
    )

    await bus.publish(env)
    await bus.drain(timeout=2.0)

    # Invariants for Control Arm
    assert handles.decisions_executed == 0, "Control arm must not increment decisions_executed"
    assert handles.mutations_applied == 0, "Control arm must not increment mutations_applied"
    assert not deception.execute_async.called, "Deception engine execute must not be called in dry run"
    assert "sess_ctrl" not in handles.applied_mutations, "No applied mutations may be recorded for control arm"


@pytest.mark.asyncio
async def test_treatment_arm_successful_mutation_metrics() -> None:
    """Verify that a Treatment arm run produces decisions_executed=1 and mutations_applied=1."""
    bus = EventBus()
    fusion = MagicMock()
    policy = MagicMock()
    deception = MagicMock()

    mock_mutation = MutationResult(
        mutation_id="mut_trt_test",
        session_id="sess_trt",
        correlation_id="corr_trt",
        decision_id="dec_trt",
        primitive="test@1.0",
        status=MutationStatus.APPLIED,
        latency_ms=2.5,
        changes=[],
    )
    deception.execute_async = AsyncMock(return_value=mock_mutation)

    async def _resolve_dry_run(_sid: str) -> bool:
        return False  # Treatment arm

    handles = wire_engines(
        bus,
        fusion=fusion,
        policy=policy,
        deception=deception,
        resolve_dry_run=_resolve_dry_run,
    )
    await bus.start()

    decision = PolicyDecision(
        decision_id="dec_trt_1",
        session_id="sess_trt",
        correlation_id="corr_trt_1",
        rule_id="RULE-002",
        rule_version="1.0",
        triggered_by="sem_trt_1",
        verdict=Verdict.EXECUTE,
        action="TEST_ACTION",
        parameters={},
        rationale="testing treatment execution",
    )
    env = Envelope[PolicyDecision](
        message_id=str(uuid.uuid4()),
        message_type="PolicyDecision",
        session_id="sess_trt",
        correlation_id="corr_trt_1",
        emitted_at=datetime.now(timezone.utc),
        emitter="PolicyEngine",
        payload=decision,
    )

    await bus.publish(env)
    await bus.drain(timeout=2.0)

    # Invariants for Treatment Arm
    assert handles.decisions_executed == 1, "Treatment arm must increment decisions_executed"
    assert handles.mutations_applied == 1, "Treatment arm with APPLIED mutation must increment mutations_applied"
    assert deception.execute_async.called, "Deception engine execute must be called for treatment arm"
    assert "sess_trt" in handles.applied_mutations
    assert len(handles.applied_mutations["sess_trt"]) == 1
    assert handles.applied_mutations["sess_trt"][0][0] == "mut_trt_test"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MutationStatus.FAILED, MutationStatus.SKIPPED])
async def test_treatment_arm_non_applied_mutation_metrics(status: MutationStatus) -> None:
    """Verify that if a mutation FAILS or is SKIPPED, decisions_executed increments but mutations_applied does NOT."""
    bus = EventBus()
    fusion = MagicMock()
    policy = MagicMock()
    deception = MagicMock()

    mock_mutation = MutationResult(
        mutation_id=f"mut_{status.value}",
        session_id="sess_trt_non_applied",
        correlation_id="corr_trt",
        decision_id="dec_trt",
        primitive="test@1.0",
        status=status,
        error="Target unavailable or skipped",
        latency_ms=1.0,
        changes=[],
    )
    deception.execute_async = AsyncMock(return_value=mock_mutation)

    async def _resolve_dry_run(_sid: str) -> bool:
        return False

    handles = wire_engines(
        bus,
        fusion=fusion,
        policy=policy,
        deception=deception,
        resolve_dry_run=_resolve_dry_run,
    )
    await bus.start()

    decision = PolicyDecision(
        decision_id="dec_trt_2",
        session_id="sess_trt_non_applied",
        correlation_id="corr_trt_2",
        rule_id="RULE-003",
        rule_version="1.0",
        triggered_by="sem_trt_2",
        verdict=Verdict.EXECUTE,
        action="TEST_ACTION",
        parameters={},
        rationale="testing non-applied mutation",
    )
    env = Envelope[PolicyDecision](
        message_id=str(uuid.uuid4()),
        message_type="PolicyDecision",
        session_id="sess_trt_non_applied",
        correlation_id="corr_trt_2",
        emitted_at=datetime.now(timezone.utc),
        emitter="PolicyEngine",
        payload=decision,
    )

    await bus.publish(env)
    await bus.drain(timeout=2.0)

    assert handles.decisions_executed == 1
    assert handles.mutations_applied == 0, f"Mutation with status {status.value} must NOT increment mutations_applied"
    assert "sess_trt_non_applied" not in handles.applied_mutations


# ============================================================================ #
# 2. API Event Handler Metric Isolation (`store_event`)
# ============================================================================ #


@pytest.mark.asyncio
async def test_api_store_event_metric_isolation() -> None:
    """Verify store_event in adam/api/main.py enforces arm and mutation status checks."""
    sess_id_ctrl = "sess_api_ctrl"
    sess_id_trt = "sess_api_trt"

    sample = SampleRef(
        sha256="0" * 64, md5="0" * 32, filename="sample.exe", size_bytes=100, file_type="binary"
    )

    meta_ctrl = AnalysisSession(
        session_id=sess_id_ctrl,
        experiment_id="exp_api",
        arm=Arm.CONTROL,
        sample=sample,
        config=SessionConfig(deception_enabled=False, policy_ruleset="default", vm_profile="win10", timeout_seconds=60, network_mode=NetworkMode.SIMULATED),
        status=SessionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        metrics=SessionMetrics(),
    )
    sessions_store[sess_id_ctrl] = {
        "metadata": meta_ctrl,
        "events": [],
        "decisions": [],
        "mutations": [],
    }

    meta_trt = AnalysisSession(
        session_id=sess_id_trt,
        experiment_id="exp_api",
        arm=Arm.TREATMENT,
        sample=sample,
        config=SessionConfig(deception_enabled=True, policy_ruleset="default", vm_profile="win10", timeout_seconds=60, network_mode=NetworkMode.SIMULATED),
        status=SessionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        metrics=SessionMetrics(),
    )
    sessions_store[sess_id_trt] = {
        "metadata": meta_trt,
        "events": [],
        "decisions": [],
        "mutations": [],
    }

    dec_ctrl = PolicyDecision(
        decision_id="dec_1", session_id=sess_id_ctrl, correlation_id="c1", rule_id="R1", rule_version="1.0",
        triggered_by="s1", verdict=Verdict.EXECUTE, action="ACT", parameters={}, rationale="r"
    )
    env_dec_ctrl = Envelope[PolicyDecision](
        message_id="m1", message_type="PolicyDecision", session_id=sess_id_ctrl, correlation_id="c1",
        emitted_at=datetime.now(timezone.utc), emitter="p", payload=dec_ctrl
    )

    dec_trt = PolicyDecision(
        decision_id="dec_2", session_id=sess_id_trt, correlation_id="c2", rule_id="R2", rule_version="1.0",
        triggered_by="s2", verdict=Verdict.EXECUTE, action="ACT", parameters={}, rationale="r"
    )
    env_dec_trt = Envelope[PolicyDecision](
        message_id="m2", message_type="PolicyDecision", session_id=sess_id_trt, correlation_id="c2",
        emitted_at=datetime.now(timezone.utc), emitter="p", payload=dec_trt
    )

    await store_event(env_dec_ctrl)
    await store_event(env_dec_trt)

    # Control arm should not count decisions_executed
    assert meta_ctrl.metrics.decisions_total == 1
    assert meta_ctrl.metrics.decisions_executed == 0
    # Treatment arm should count decisions_executed
    assert meta_trt.metrics.decisions_total == 1
    assert meta_trt.metrics.decisions_executed == 1

    # Test mutation results into treatment session:
    mut_failed = MutationResult(
        mutation_id="m_fail", session_id=sess_id_trt, correlation_id="c2", decision_id="dec_2",
        primitive="test@1.0", status=MutationStatus.FAILED, latency_ms=1.0, changes=[]
    )
    env_mut_failed = Envelope[MutationResult](
        message_id="mf", message_type="MutationResult", session_id=sess_id_trt, correlation_id="c2",
        emitted_at=datetime.now(timezone.utc), emitter="d", payload=mut_failed
    )
    await store_event(env_mut_failed)
    assert meta_trt.metrics.mutations_applied == 0, "FAILED mutation must not increment mutations_applied"

    mut_skipped = MutationResult(
        mutation_id="m_skip", session_id=sess_id_trt, correlation_id="c2", decision_id="dec_2",
        primitive="test@1.0", status=MutationStatus.SKIPPED, latency_ms=1.0, changes=[]
    )
    env_mut_skipped = Envelope[MutationResult](
        message_id="ms", message_type="MutationResult", session_id=sess_id_trt, correlation_id="c2",
        emitted_at=datetime.now(timezone.utc), emitter="d", payload=mut_skipped
    )
    await store_event(env_mut_skipped)
    assert meta_trt.metrics.mutations_applied == 0, "SKIPPED mutation must not increment mutations_applied"

    mut_applied = MutationResult(
        mutation_id="m_app", session_id=sess_id_trt, correlation_id="c2", decision_id="dec_2",
        primitive="test@1.0", status=MutationStatus.APPLIED, latency_ms=1.0, changes=[]
    )
    env_mut_applied = Envelope[MutationResult](
        message_id="ma", message_type="MutationResult", session_id=sess_id_trt, correlation_id="c2",
        emitted_at=datetime.now(timezone.utc), emitter="d", payload=mut_applied
    )
    await store_event(env_mut_applied)
    assert meta_trt.metrics.mutations_applied == 1, "APPLIED mutation must increment mutations_applied"

    # Clean up sessions_store
    del sessions_store[sess_id_ctrl]
    del sessions_store[sess_id_trt]


# ============================================================================ #
# 3. FabricateC2Response Missing URL Handling
# ============================================================================ #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"target_url": None},
        {"c2_url": None},
        {"target_url": ""},
        {"c2_url": ""},
        {"target_url": "ftp://malicious.org/payload"},
        {"target_url": "gopher://evil.com"},
        {"target_url": 12345},
    ],
)
async def test_fabricate_c2_missing_or_invalid_url_skipped(params: dict) -> None:
    """Verify missing, invalid, or non-HTTP C2 URLs produce MutationStatus.SKIPPED and no uncaught exception."""
    channel = AsyncMock()
    prim = FabricateC2Response(channel)

    res = await prim.apply_async("sess_c2", "corr_c2", "dec_c2", params)

    assert res.status == MutationStatus.SKIPPED
    assert res.revertible is False
    assert "No observed HTTP C2 endpoint in session" in (res.error or "")
    assert not channel.apply_mutation.called


@pytest.mark.asyncio
async def test_fabricate_c2_engine_execution_without_endpoint() -> None:
    """Verify DeceptionEngine execution of FABRICATE_C2_RESPONSE without target_url produces MutationStatus.SKIPPED."""
    channel = AsyncMock()
    engine = DeceptionEngine(channel)

    decision = PolicyDecision(
        decision_id="dec_c2_engine",
        session_id="sess_c2_engine",
        correlation_id="corr_c2_engine",
        rule_id="RULE-023",
        rule_version="1.0",
        triggered_by="sem_c2",
        verdict=Verdict.EXECUTE,
        action="FABRICATE_C2_RESPONSE",
        parameters={},
        rationale="c2 observed",
    )

    res = await engine.execute_async(decision)
    assert res.status == MutationStatus.SKIPPED
    assert "No observed HTTP C2 endpoint in session" in (res.error or "")
    assert res.revertible is False
    assert not channel.apply_mutation.called


@pytest.mark.asyncio
async def test_fabricate_c2_valid_url_applied() -> None:
    """Verify valid HTTP C2 URL produces MutationStatus.APPLIED with revertible=True."""
    channel = AsyncMock()
    channel.apply_mutation = AsyncMock(return_value={"status": "ok"})
    prim = FabricateC2Response(channel)

    res = await prim.apply_async("sess_c2", "corr_c2", "dec_c2", {"target_url": "http://192.168.19.10:8080/beacon"})
    assert res.status == MutationStatus.APPLIED
    assert res.revertible is True
    assert res.error is None
    assert channel.apply_mutation.called


# ============================================================================ #
# 4. Truthful Session Status on Teardown Failure
# ============================================================================ #


@pytest.mark.asyncio
async def test_runner_teardown_failure_degrades_status_to_partial() -> None:
    """Verify that failures in runner teardown operations degrade COMPLETED status to PARTIAL."""
    runner = Runner()

    mock_session = AnalysisSession(
        session_id="sess_runner_teardown_test",
        experiment_id="exp_teardown",
        arm=Arm.TREATMENT,
        sample=SampleRef(sha256="0" * 64, md5="0" * 32, filename="sample.exe", size_bytes=100, file_type="exe"),
        config=SessionConfig(deception_enabled=True, policy_ruleset="default", vm_profile="default", timeout_seconds=100, network_mode=NetworkMode.HOST_ONLY),
        status=SessionStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        metrics=SessionMetrics(),
    )

    with patch("adam.orchestrator.runner.SessionOrchestrator") as mock_orch_cls, \
         patch("adam.orchestrator.runner.sample_ref_from_path") as mock_sample, \
         patch("adam.orchestrator.runner.VirtualBoxClient"), \
         patch("adam.orchestrator.runner.SandboxController"), \
         patch("adam.orchestrator.runner.wire_engines"), \
         patch("adam.orchestrator.runner.aiosqlite.connect") as mock_sqlite:

        mock_conn = AsyncMock()
        mock_sqlite.return_value = mock_conn
        mock_sample.return_value = mock_session.sample

        mock_orch = AsyncMock()
        mock_orch.run_session.return_value = mock_session
        mock_orch_cls.return_value = mock_orch

        # Inject teardown failures: interceptor stop fails, guest channel aclose fails
        runtime = MagicMock()
        runtime.interceptor = MagicMock()
        runtime.interceptor.is_active = True
        runtime.interceptor.start = AsyncMock()
        runtime.interceptor.stop = AsyncMock(side_effect=RuntimeError("interceptor stop timeout"))
        runtime.channel = MagicMock()
        runtime.channel.aclose = AsyncMock(side_effect=RuntimeError("channel close reset"))
        runner._build_guest_channel = MagicMock(return_value=runtime)

        result = await runner.run("dummy.exe")

        assert result.status == SessionStatus.PARTIAL, "Status must degrade to PARTIAL on teardown failure"
        assert "interceptor stop timeout" in (result.error or "")
        assert "channel close reset" in (result.error or "")


@pytest.mark.asyncio
async def test_session_orchestrator_collector_start_failure_marks_failed() -> None:
    """Verify that if a collector start fails before detonation, session status is marked FAILED."""
    controller = AsyncMock()
    controller.vm_profile = "default"
    bus = AsyncMock()

    failing_collector = MagicMock()
    failing_collector.source_name = "sysmon"
    failing_collector.start = AsyncMock(side_effect=RuntimeError("Sysmon driver unavailable"))
    failing_collector.stop = AsyncMock()

    orch = SessionOrchestrator(controller, bus, [failing_collector], artifacts_dir="artifacts", bus_drain_timeout=1.0)
    sample = SampleRef(sha256="0" * 64, md5="0" * 32, filename="sample.exe", size_bytes=100, file_type="exe")
    from adam.common.config import Settings
    config = Settings()

    session = await orch.run_session(sample, config, host_sample_path="dummy.exe")
    assert session.status == SessionStatus.FAILED
    assert "Sysmon driver unavailable" in (session.error or "")


# ============================================================================ #
# 5. Report Generator Structured Telemetry Outcomes & Failure Details
# ============================================================================ #


@pytest.mark.asyncio
async def test_report_generator_structured_outcomes_and_failures() -> None:
    """Verify that ReportGenerator outputs per-source telemetry outcomes and structured failure details."""
    session_id = "sess_report_test"
    sample = SampleRef(sha256="0" * 64, md5="0" * 32, filename="sample.exe", size_bytes=100, file_type="exe")
    session = AnalysisSession(
        session_id=session_id,
        experiment_id="exp_rep",
        arm=Arm.TREATMENT,
        sample=sample,
        config=SessionConfig(deception_enabled=True, policy_ruleset="default", vm_profile="win10", timeout_seconds=100, network_mode=NetworkMode.HOST_ONLY),
        status=SessionStatus.PARTIAL,
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        metrics=SessionMetrics(),
        error="Intermittent network drop during capture",
    )

    now = datetime.now(timezone.utc)
    raw_events = [
        RawEvent(
            event_id="e1", session_id=session_id, source=Source.SYSMON, source_event_id=1,
            category=Category.PROCESS, occurred_at=now, observed_at=now
        ),
        RawEvent(
            event_id="e2", session_id=session_id, source=Source.SYSMON, source_event_id=2,
            category=Category.PROCESS, occurred_at=now, observed_at=now
        ),
        RawEvent(
            event_id="e3", session_id=session_id, source=Source.PROCMON, source_event_id=10,
            category=Category.FILE, occurred_at=now, observed_at=now
        ),
    ]

    failed_mut = MutationResult(
        mutation_id="mut_fail_1", session_id=session_id, correlation_id="c1", decision_id="d1",
        primitive="SpawnDecoyProcesses@1.0", status=MutationStatus.FAILED, error="Access denied",
        latency_ms=5.0, changes=[]
    )

    session_repo = AsyncMock()
    session_repo.get_by_id = AsyncMock(return_value=session)
    event_repo = AsyncMock()
    event_repo.get_semantic_by_session = AsyncMock(return_value=[])
    event_repo.get_raw_by_session = AsyncMock(return_value=raw_events)
    decision_repo = AsyncMock()
    decision_repo.get_by_session = AsyncMock(return_value=[])
    mutation_repo = AsyncMock()
    mutation_repo.get_by_session = AsyncMock(return_value=[failed_mut])

    gen = ReportGenerator(
        session_repo=session_repo,
        event_repo=event_repo,
        decision_repo=decision_repo,
        mutation_repo=mutation_repo,
    )

    report_json_str = await gen.generate(session_id, format="json")
    data = json.loads(report_json_str)

    # Validate structured source outcomes
    assert "source_outcomes" in data
    sources = data["source_outcomes"]
    assert sources["Sysmon"]["events_count"] == 2
    assert sources["Sysmon"]["status"] == "CAPTURED"
    assert sources["Procmon"]["events_count"] == 1
    assert sources["Procmon"]["status"] == "CAPTURED"
    assert sources["Tshark"]["events_count"] == 0
    assert sources["Tshark"]["status"] == "NO_DATA"

    # Validate structured failures list
    assert "failures" in data
    failures = data["failures"]
    assert len(failures) == 2
    types = [f["type"] for f in failures]
    assert "SessionError" in types
    assert "MutationFailure" in types
    sess_err = next(f for f in failures if f["type"] == "SessionError")
    assert "Intermittent network drop" in sess_err["detail"]
    mut_err = next(f for f in failures if f["type"] == "MutationFailure")
    assert "Access denied" in mut_err["detail"]


# ============================================================================ #
# 6. Candidate Snapshot Verification Orphan VDI Checker
# ============================================================================ #


def test_orphan_vdi_check_detection(tmp_path: Path) -> None:
    """Verify check_orphan_vdi correctly detects missing files, baselines, and changes."""
    nonexistent = tmp_path / "does_not_exist.vdi"
    res1 = check_orphan_vdi(nonexistent)
    assert res1["exists"] is False
    assert res1["untouched"] is False

    test_vdi = tmp_path / "test.vdi"
    test_vdi.write_bytes(b"A" * 1024)

    res2 = check_orphan_vdi(test_vdi)
    assert res2["exists"] is True
    assert res2["untouched"] is True
    assert res2["size_bytes"] == 1024

    baseline_stat = test_vdi.stat()
    # Check untouched with baseline
    res3 = check_orphan_vdi(test_vdi, baseline_stat=baseline_stat)
    assert res3["untouched"] is True

    # Mutate file and check that check_orphan_vdi detects alteration
    test_vdi.write_bytes(b"B" * 2048)
    res4 = check_orphan_vdi(test_vdi, baseline_stat=baseline_stat)
    assert res4["untouched"] is False
    assert res4["size_bytes"] == 2048


# ============================================================================ #
# 7. Strict Dashboard Status Codes Contract Verification
# ============================================================================ #


def test_dashboard_strict_status_contract() -> None:
    """Verify that dashboard endpoints return 200 for valid data, 404 for missing data, and never mask 500."""
    from fastapi.testclient import TestClient
    from adam.api.main import app

    with TestClient(app) as client:
        # 1. Missing session report -> must be 404, not 500 or 200
        res_rep_404 = client.get("/dashboard/sessions/completely_nonexistent_session/report")
        assert res_rep_404.status_code == 404, f"Expected 404 for nonexistent session report, got {res_rep_404.status_code}"

        # 2. Missing comparison -> must be 404, not 500 or 200
        res_comp_404 = client.get("/dashboard/experiments/completely_nonexistent_experiment/comparison")
        assert res_comp_404.status_code == 404, f"Expected 404 for nonexistent experiment comparison, got {res_comp_404.status_code}"

