"""Console contracts exercise real persistence with a controlled Runner, never a VM."""
import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
from fastapi import FastAPI

from adam.api import console
from adam.api.deps import deps
from adam.contracts.enums import Arm, NetworkMode, SessionStatus
from adam.contracts.session import AnalysisSession, SessionConfig, SessionMetrics
from adam.db.repositories.sqlite import SQLiteSessionRepository
from adam.db.schema import SCHEMA_SQL
from adam.orchestrator.runner import sample_ref_from_path


@pytest.fixture
async def console_client(tmp_path, monkeypatch):
    root = tmp_path
    (root / "samples").mkdir()
    (root / "samples/smoke_sample.exe").write_bytes(b"MZcontrolled-fixture-not-executed")
    connection = await aiosqlite.connect(root / "console.db")
    await connection.executescript(SCHEMA_SQL)
    monkeypatch.setattr(console, "ROOT", root)
    monkeypatch.setattr(console, "operation_lock", asyncio.Lock())
    monkeypatch.setattr(console, "jobs", {})
    monkeypatch.setattr(console, "tasks", set())
    monkeypatch.setattr(console, "listeners", {})
    monkeypatch.setattr(deps, "db_conn", connection)
    monkeypatch.setattr(deps, "session_repo", SQLiteSessionRepository(connection))
    await console.init_console()
    app = FastAPI()
    app.include_router(console.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://console") as client:
        yield client, root, connection
    await console.shutdown_jobs()
    await connection.close()


async def finish_jobs():
    if console.tasks:
        await asyncio.gather(*list(console.tasks))


def controlled_runner(monkeypatch, *, gate=None, error=None, paths=None):
    class ControlledRunner:
        def __init__(self, settings):
            pass

        async def run(self, path, **kwargs):
            if paths is not None:
                paths.append(path)
            if gate:
                await gate.wait()
            if error:
                raise error
            metadata = console.jobs[kwargs["session_id"]]["metadata"].model_copy(deep=True)
            lifecycle = SimpleNamespace(message_type="SessionLifecycle", payload=SimpleNamespace(
                model_dump=lambda **_: {"session_id": metadata.session_id, "status": "COMPLETED"}))
            await kwargs["event_observer"](lifecycle)
            # No terminal notification is allowed before Runner returns.
            assert console.jobs[metadata.session_id]["metadata"].status == SessionStatus.PREPARING
            metadata.status = SessionStatus.COMPLETED
            metadata.ended_at = datetime.now(UTC)
            await deps.session_repo.create(metadata)
            await deps.db_conn.commit()
            return metadata
    monkeypatch.setattr(console, "Runner", ControlledRunner)


@pytest.mark.asyncio
async def test_launch_returns_real_id_and_rejects_concurrent_operations(console_client, monkeypatch):
    client, root, _connection = console_client
    gate = asyncio.Event()
    paths = []
    controlled_runner(monkeypatch, gate=gate, paths=paths)
    response = await client.post("/api/v1/sessions/live?sample_id=smoke")
    assert response.status_code == 202
    sid = response.json()["session_id"]
    assert sid.startswith("sess_")
    assert (await client.post("/api/v1/sessions/live?sample_id=smoke")).status_code == 409
    assert (await client.post("/api/v1/sandbox/reset")).status_code == 409
    pending = (await client.get(f"/api/v1/sessions/{sid}/summary")).json()
    assert pending["provisional"] and pending["metadata"]["arm"] == "CONTROL"
    gate.set()
    await finish_jobs()
    final = (await client.get(f"/api/v1/sessions/{sid}/summary")).json()
    assert final["metadata"]["status"] == "COMPLETED"
    assert not final["provisional"] and not console.operation_lock.locked()
    assert paths == [str(root / "samples/smoke_sample.exe")]


@pytest.mark.asyncio
async def test_startup_failure_is_persisted_and_retry_is_possible(console_client, monkeypatch):
    client, root, _connection = console_client
    controlled_runner(monkeypatch, error=RuntimeError("Guest agent readiness timed out"))
    sid = (await client.post("/api/v1/sessions/live?sample_id=smoke")).json()["session_id"]
    await finish_jobs()
    response = await client.get(f"/api/v1/sessions/{sid}/summary")
    result = response.json()
    assert result["metadata"]["status"] == "FAILED"
    assert result["metadata"]["failures"][0]["stage"] == "api"
    assert result["artifacts"] == []
    stored = await deps.session_repo.get_by_id(sid)
    artifact = json.loads((root / "artifacts" / sid / "session.json").read_text())
    assert stored.model_dump(mode="json") == artifact == result["metadata"]
    assert not console.operation_lock.locked()
    controlled_runner(monkeypatch)
    retry = await client.post("/api/v1/sessions/live?sample_id=smoke")
    assert retry.status_code == 202 and retry.json()["session_id"] != sid
    await finish_jobs()


@pytest.mark.asyncio
async def test_uploads_are_real_unique_and_filenames_are_contained(console_client, monkeypatch):
    client, root, _connection = console_client
    paths = []
    controlled_runner(monkeypatch, paths=paths)
    for payload in (b"first-real-upload", b"second-real-upload"):
        response = await client.post("/api/v1/sessions/live", files={"file": ("../../sample.exe", payload)})
        assert response.status_code == 202
        await finish_jobs()
    assert paths[0] != paths[1]
    from pathlib import Path
    assert all(Path(path).is_relative_to(root / "samples/uploaded") for path in paths)
    assert Path(paths[0]).read_bytes() == b"first-real-upload"
    assert Path(paths[1]).read_bytes() == b"second-real-upload"
    assert (await client.post("/api/v1/sessions/live")).status_code == 400
    assert (await client.post("/api/v1/sessions/live?sample_id=smoke", files={"file": ("x", b"x")})).status_code == 400
    assert not console.operation_lock.locked()


@pytest.mark.asyncio
async def test_bounded_reads_complete_summaries_and_artifacts(console_client):
    client, root, connection = console_client
    sid = "sess_console_records"
    metadata = AnalysisSession(session_id=sid, experiment_id="exp_console", arm=Arm.CONTROL,
        sample=sample_ref_from_path(str(root / "samples/smoke_sample.exe")),
        config=SessionConfig(deception_enabled=False, policy_ruleset="default", vm_profile="Windows",
            timeout_seconds=300, network_mode=NetworkMode.HOST_ONLY), status=SessionStatus.COMPLETED,
        started_at=datetime.now(UTC), metrics=SessionMetrics(semantic_events=6))
    await deps.session_repo.create(metadata)
    for i in range(6):
        payload = {"semantic_id": f"event_{i}", "intent": "C2_BEACON", "attck": {"technique": f"T10{i:02}", "tactic": "TA0011"},
                   "features": {"network_endpoint": f"http://controlled/{i}"}}
        await connection.execute("INSERT INTO semantic_events VALUES(?,?,?,?,?,?,?,?)",
            (f"event_{i}", sid, "correlation", "C2_BEACON", .9, "2026-11-05", None, json.dumps(payload)))
    await connection.commit()
    directory = root / "artifacts/terminal-run" / sid
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(metadata.model_dump_json())
    (directory / "raw.jsonl").write_text('{"actual":"artifact"}\n')
    page = (await client.get(f"/api/v1/sessions/{sid}/telemetry/events?offset=2&limit=2")).json()
    assert [item["semantic_id"] for item in page["items"]] == ["event_2", "event_3"]
    assert page["total"] == 6 and page["next_offset"] == 4
    summary = (await client.get(f"/api/v1/sessions/{sid}/summary")).json()
    assert len(summary["attck"]) == 6 and len(summary["indicators"]) == 6
    assert summary["counts"]["events"] == 6
    assert [item["kind"] for item in summary["artifacts"]] == ["raw"]
    assert not summary["comparison_available"]
    assert (await client.get(f"/api/v1/sessions/{sid}/artifacts/raw")).content == (directory / "raw.jsonl").read_bytes()
    assert (await client.get(f"/api/v1/sessions/{sid}/artifacts/sysmon")).status_code == 404
    assert (await client.get(f"/api/v1/sessions/{sid}/telemetry/events?limit=10000")).status_code == 422
    assert (await client.get("/api/v1/console/sessions?q=does-not-exist")).json()["total"] == 0
    assert (await client.get("/api/v1/console/sessions?mode=simulation")).json()["total"] == 0


@pytest.mark.asyncio
async def test_reset_failure_preserves_baseline_and_never_forces_poweroff(console_client, monkeypatch):
    client, _root, _connection = console_client
    vbox = SimpleNamespace(get_state=AsyncMock(return_value="running"), restore_snapshot=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(console, "VirtualBoxClient", lambda: vbox)
    channel = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(console, "guest_channel", lambda _: channel)
    monkeypatch.setattr(console, "shutdown_normally", AsyncMock(return_value=False))
    response = await client.post("/api/v1/sandbox/reset")
    assert response.status_code == 409
    vbox.restore_snapshot.assert_not_called()
    vbox.stop.assert_not_called()
    assert not console.operation_lock.locked()
    channel.aclose.assert_awaited_once()


def test_stream_backpressure_requests_resync_without_blocking(monkeypatch):
    slow = asyncio.Queue(maxsize=1)
    healthy = asyncio.Queue(maxsize=5)
    monkeypatch.setattr(console, "listeners", {slow: "sess_a", healthy: "sess_a"})
    console.broadcast("SemanticEvent", {"session_id": "sess_a", "semantic_id": "one"})
    console.broadcast("SemanticEvent", {"session_id": "sess_a", "semantic_id": "two"})
    assert "ResyncRequired" in slow.get_nowait()
    assert healthy.qsize() == 2
    console.broadcast("SemanticEvent", {"session_id": "other"})
    assert healthy.qsize() == 2
