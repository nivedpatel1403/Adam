"""Local console integration: real Runner jobs, bounded reads, and evidence files."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from adam.common.config import get_settings
from adam.contracts.enums import Arm, NetworkMode, SessionStatus
from adam.contracts.session import AnalysisSession, SessionConfig, SessionFailure, SessionMetrics
from adam.orchestrator.runner import Runner, sample_ref_from_path
from adam.orchestrator.session import new_session_id
from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.shutdown import shutdown_normally
from adam.sandbox.vbox.client import VirtualBoxClient

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
operation_lock = asyncio.Lock()
jobs: dict[str, dict[str, Any]] = {}
tasks: set[asyncio.Task] = set()
listeners: dict[asyncio.Queue, str | None] = {}
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "ABORTED"}
KINDS = {
    "raw": ("raw.jsonl", "application/x-ndjson"),
    "sysmon": ("sysmon.evtx", "application/octet-stream"),
    "procmon": ("procmon.csv", "text/csv"),
    "network": ("network.pcapng", "application/octet-stream"),
    "network-json": ("network.ek.json", "application/x-ndjson"),
}
TABLES = {
    "events": ("semantic_events", "window_start", "semantic_id"),
    "decisions": ("policy_decisions", "decided_at", "decision_id"),
    "mutations": ("mutations", "applied_at", "mutation_id"),
}


def guest_channel(settings) -> HTTPGuestChannel:
    guest = settings.http_guest
    return HTTPGuestChannel(base_url=guest.base_url, capture_dir=guest.capture_dir,
        procmon_path=guest.procmon_path, tshark_path=guest.tshark_path,
        sysmon_log=guest.sysmon_log, tshark_interface=guest.tshark_interface,
        auth_token=guest.auth_token, request_timeout_s=2, retry_attempts=1)


def valid_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise HTTPException(400, "Invalid session identifier")
    return value


def broadcast(event: str, payload: dict) -> None:
    message = f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
    for queue, session_id in list(listeners.items()):
        if session_id and payload.get("session_id") != session_id:
            continue
        if queue.full():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait('event: ResyncRequired\ndata: {}\n\n')
        else:
            queue.put_nowait(message)


async def shutdown_jobs() -> None:
    pending = list(tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def init_console() -> None:
    from adam.api.deps import deps
    for kind, (table, timestamp, identity) in TABLES.items():
        await deps.db_conn.execute(f"CREATE INDEX IF NOT EXISTS idx_console_{kind} ON {table}(session_id,{timestamp},{identity})")
    await deps.db_conn.commit()


async def database_rows(sql: str, parameters: tuple = ()) -> list:
    from adam.api.deps import deps
    async with deps.db_conn.execute(sql, parameters) as cursor:
        return await cursor.fetchall()


def mode(metadata: AnalysisSession) -> str:
    if metadata.session_id.startswith("replay_"):
        return "replay"
    if metadata.config.network_mode == NetworkMode.SIMULATED:
        return "simulation"
    return "live" if metadata.session_id.startswith("sess_") else "unknown"


async def metadata_for(session_id: str) -> AnalysisSession:
    from adam.api.deps import deps
    from adam.api.main import sessions_store
    valid_id(session_id)
    stored = await deps.session_repo.get_by_id(session_id)
    cached = jobs.get(session_id, {}).get("metadata")
    if cached is not None and not jobs[session_id].get("finished"):
        return cached
    result = stored or cached or sessions_store.get(session_id, {}).get("metadata")
    if result is None:
        raise HTTPException(404, "Session not found")
    return result


def inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


async def artifact_directory(session_id: str) -> Path | None:
    valid_id(session_id)
    roots = (ROOT / "artifacts", ROOT / "logs" / "repair-verification")
    registered = await database_rows("SELECT path FROM artifacts WHERE session_id=? LIMIT 1", (session_id,))
    candidates = [Path(registered[0][0]).parent] if registered else []
    if session_id in jobs:
        candidates.append(jobs[session_id]["directory"])
    candidates.append(roots[0] / session_id)
    for candidate in candidates:
        if any(inside(candidate, root) for root in roots) and candidate.is_dir():
            return candidate.resolve()
    # Legacy CLI sessions can use a custom artifacts directory. Search only the
    # known archive layouts, never a client-controlled path or the whole disk.
    for root in roots:
        for pattern in (f"*/{session_id}/session.json", f"*/*/{session_id}/session.json",
                        f"*/*/*/{session_id}/session.json"):
            candidates.extend(file.parent for file in root.glob(pattern))
    for candidate in candidates:
        if any(inside(candidate, root) for root in roots) and candidate.is_dir():
            return candidate.resolve()
    return None


@router.get("/samples")
async def samples():
    sample = ROOT / "samples" / "smoke_sample.exe"
    available = []
    if sample.is_file():
        ref = await asyncio.to_thread(sample_ref_from_path, str(sample))
        available.append({"id": "smoke", "filename": sample.name,
                          "size_bytes": ref.size_bytes, "sha256": ref.sha256})
    return {"samples": available}


@router.get("/console/sessions")
async def archive(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  q: str = "", mode_filter: str = Query("all", alias="mode")):
    from adam.api.deps import deps
    from adam.api.main import sessions_store
    records = {s.session_id: s for s in await deps.session_repo.list_all()}
    for sid, entry in sessions_store.items():
        if entry.get("metadata") is not None:
            records.setdefault(sid, entry["metadata"])
    for sid, job in jobs.items():
        records[sid] = job["metadata"]
    ordered = sorted(records.values(), key=lambda record: record.started_at, reverse=True)
    if mode_filter not in ("all", "live", "replay", "simulation", "unknown"):
        raise HTTPException(400, "Unknown session origin")
    ordered = [s for s in ordered if (mode_filter == "all" or mode(s) == mode_filter)
               and (not q or q.lower() in s.session_id.lower() or q.lower() in s.sample.filename.lower())]
    return {"items": [{"metadata": s.model_dump(mode="json"), "mode": mode(s)}
                       for s in ordered[offset:offset + limit]], "total": len(ordered),
            "next_offset": offset + limit if offset + limit < len(ordered) else None}


@router.get("/sessions/{session_id}/summary")
async def summary(session_id: str):
    metadata = await metadata_for(session_id)
    counts = {}
    for kind, (table, _, _) in TABLES.items():
        counts[kind] = (await database_rows(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (session_id,)))[0][0]
    provisional = session_id in jobs and not jobs[session_id].get("finished", False)
    if provisional:
        metadata = metadata.model_copy(deep=True)
        metadata.metrics.semantic_events = counts["events"]
        metadata.metrics.decisions_total = counts["decisions"]
        metadata.metrics.mutations_applied = (await database_rows(
            "SELECT COUNT(*) FROM mutations WHERE session_id=? AND status='APPLIED'", (session_id,)))[0][0]
    techniques = await database_rows(
        "SELECT DISTINCT json_extract(payload,'$.attck.technique'), "
        "json_extract(payload,'$.attck.tactic') FROM semantic_events WHERE session_id=? "
        "AND json_extract(payload,'$.attck.technique') IS NOT NULL ORDER BY 1", (session_id,))
    indicators = await database_rows(
        "SELECT DISTINCT COALESCE(json_extract(payload,'$.features.target_object'), "
        "json_extract(payload,'$.features.network_endpoint'), json_extract(payload,'$.features.TargetFilename'), "
        "json_extract(payload,'$.features.TargetObject'), json_extract(payload,'$.features.QueryName')) "
        "FROM semantic_events WHERE session_id=? ORDER BY 1 LIMIT 101", (session_id,))
    targets = [{"source": "Observed", "target": row[0]} for row in indicators if row[0]]
    mutation_rows = await database_rows("SELECT payload FROM mutations WHERE session_id=? ORDER BY applied_at LIMIT 100", (session_id,))
    for row in mutation_rows:
        mutation = json.loads(row[0])
        for change in mutation.get("changes", []):
            targets.append({"source": "Mutation", "target": change["target"],
                            "status": mutation["status"]})
    directory = await artifact_directory(session_id)
    artifacts = []
    if directory:
        for kind, (filename, _) in KINDS.items():
            path = directory / filename
            if path.is_file() and inside(path, directory):
                artifacts.append({"kind": kind, "filename": filename, "size_bytes": path.stat().st_size,
                                  "url": f"/api/v1/sessions/{session_id}/artifacts/{kind}"})
    arms = await database_rows("SELECT DISTINCT arm FROM sessions WHERE experiment_id=?", (metadata.experiment_id,))
    return {"metadata": metadata.model_dump(mode="json"), "mode": mode(metadata), "counts": counts,
            "attck": [{"technique": t, "tactic": tactic} for t, tactic in techniques],
            "indicators": targets[:100], "indicators_truncated": len(targets) > 100,
            "artifacts": artifacts, "comparison_available": {r[0] for r in arms} >= {"CONTROL", "TREATMENT"},
            "provisional": provisional, "phase": jobs.get(session_id, {}).get("phase")}


@router.get("/sessions/{session_id}/telemetry/{kind}")
async def telemetry(session_id: str, kind: str, offset: int = Query(0, ge=0),
                    limit: int = Query(100, ge=1, le=250)):
    await metadata_for(session_id)
    if kind not in TABLES:
        raise HTTPException(400, "Unknown telemetry kind")
    table, timestamp, identity = TABLES[kind]
    total = (await database_rows(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (session_id,)))[0][0]
    rows = await database_rows(f"SELECT payload FROM {table} WHERE session_id=? ORDER BY {timestamp},{identity} LIMIT ? OFFSET ?",
                               (session_id, limit, offset))
    return {"items": [json.loads(row[0]) for row in rows], "total": total,
            "next_offset": offset + limit if offset + limit < total else None}


@router.get("/sandbox/status")
async def sandbox_status():
    settings = get_settings()
    result = {"vm_state": "unknown", "agent_healthy": False, "current_snapshot": "unknown",
              "vm_name": settings.sandbox.vm_name, "profile": settings.sandbox.vm_name,
              "busy": operation_lock.locked(), "error": None,
              "readiness_timeout_seconds": settings.sandbox.guest_ready_timeout_s}
    client = VirtualBoxClient(default_command_timeout=5)
    try:
        raw = await client.get_state(settings.sandbox.vm_name)
        result["vm_state"] = {"running": "Running", "poweroff": "Off", "starting": "Booting",
                              "restoring": "Booting", "saved": "Saved", "paused": "Paused"}.get(raw, raw)
        snapshots = await client.list_snapshots(settings.sandbox.vm_name)
        result["current_snapshot"] = next((s.name for s in snapshots if s.is_current), "unknown")
        if raw == "running":
            channel = guest_channel(settings)
            try:
                async with asyncio.timeout(3):
                    health = await channel.get_health()
                    result["agent_healthy"] = bool(health and health.status == "ok")
            finally:
                await channel.aclose()
    except Exception as exc:
        logger.warning("Sandbox status unavailable", exc_info=True)
        result["error"] = getattr(exc, "stderr", "").strip() or str(exc).splitlines()[0]
    return result


async def execute_live(session_id: str, path: Path, settings, deception: bool, timeout: int) -> None:
    job = jobs[session_id]
    job["metadata"].status = SessionStatus.PREPARING

    async def observe(envelope):
        payload = envelope.payload.model_dump(mode="json")
        if envelope.message_type == "SessionLifecycle":
            if payload["status"] in TERMINAL:
                job["phase"] = "Finalizing"
                broadcast("SessionProgress", {"session_id": session_id, "phase": "Finalizing"})
                return  # Runner still needs to commit and close its resources.
            job["metadata"].status = SessionStatus(payload["status"])
        broadcast(envelope.message_type, payload)

    try:
        final = await Runner(settings).run(str(path), session_id=session_id,
            event_observer=observe, artifacts_dir=ROOT / "artifacts", headless=True,
            arm=Arm.TREATMENT if deception else Arm.CONTROL, sample_timeout_seconds=timeout)
        job["metadata"] = final
    except (Exception, asyncio.CancelledError) as exc:
        from adam.api.deps import deps
        existing = await deps.session_repo.get_by_id(session_id)
        final = existing or job["metadata"]
        final.status = SessionStatus.ABORTED if isinstance(exc, asyncio.CancelledError) else SessionStatus.FAILED
        final.error = str(exc) or "Analysis cancelled during API shutdown"
        final.failures.append(SessionFailure(stage="api", code=type(exc).__name__, description=final.error))
        final.ended_at = datetime.now(UTC)
        if existing:
            await deps.session_repo.update(final)
        else:
            await deps.session_repo.create(final)
        await deps.db_conn.commit()
        job["metadata"] = final
        job["directory"].mkdir(parents=True, exist_ok=True)
        (job["directory"] / "session.json").write_text(final.model_dump_json(indent=2), encoding="utf-8")
        logger.exception("Console analysis ended exceptionally: %s", session_id)
    finally:
        job["finished"] = True
        from adam.api.deps import deps
        try:
            if job["directory"].exists():
                for kind, (filename, _) in KINDS.items():
                    artifact = job["directory"] / filename
                    if artifact.is_file():
                        await deps.db_conn.execute("INSERT INTO artifacts(session_id,kind,path,size_bytes) VALUES(?,?,?,?)",
                            (session_id, kind, str(artifact.resolve()), artifact.stat().st_size))
                await deps.db_conn.commit()
        except Exception:
            logger.exception("Artifact index update failed; filesystem discovery remains available")
        finally:
            operation_lock.release()
            broadcast("SessionCompleted", {"session_id": session_id, "status": job["metadata"].status.value})


@router.post("/sessions/live", status_code=202)
async def launch(file: Annotated[UploadFile | None, File()] = None, sample_id: str | None = None,
                 deception_enabled: bool = False, timeout_seconds: int = Query(300, ge=1, le=3600)):
    if (file is None) == (sample_id is None):
        raise HTTPException(400, "Choose exactly one uploaded file or built-in sample")
    if operation_lock.locked():
        raise HTTPException(409, "Another VM operation is active. Wait for it to finish.")
    await operation_lock.acquire()
    try:
        settings = get_settings()
        session_id = new_session_id()
        if file is not None:
            upload = ROOT / "samples" / "uploaded" / uuid.uuid4().hex
            upload.mkdir(parents=True)
            name = (file.filename or "sample.bin").replace("\\", "/").split("/")[-1]
            if name in ("", ".", ".."):
                raise HTTPException(400, "Invalid sample filename")
            path = upload / name
            size = 0
            with path.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise HTTPException(413, "Sample exceeds the 64 MiB upload limit")
                    stream.write(chunk)
            if not size:
                raise HTTPException(400, "The uploaded sample is empty")
        else:
            if sample_id != "smoke":
                raise HTTPException(400, "Unknown built-in sample")
            path = ROOT / "samples" / "smoke_sample.exe"
            if not path.is_file():
                raise HTTPException(404, "The built-in smoke sample is not installed")
        ref = await asyncio.to_thread(sample_ref_from_path, str(path))
        metadata = AnalysisSession(session_id=session_id, experiment_id=f"exp_{session_id}",
            arm=Arm.TREATMENT if deception_enabled else Arm.CONTROL, sample=ref,
            config=SessionConfig(deception_enabled=deception_enabled, policy_ruleset="default",
                vm_profile=settings.sandbox.vm_name, timeout_seconds=timeout_seconds, network_mode=NetworkMode.HOST_ONLY),
            status=SessionStatus.PENDING, started_at=datetime.now(UTC), metrics=SessionMetrics())
        jobs[session_id] = {"metadata": metadata, "directory": ROOT / "artifacts" / session_id, "finished": False}
        task = asyncio.create_task(execute_live(session_id, path, settings, deception_enabled, timeout_seconds))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"session_id": session_id, "status": "PENDING", "sample": ref.filename}
    except BaseException:
        operation_lock.release()
        raise


@router.post("/sandbox/reset")
async def reset():
    if operation_lock.locked():
        raise HTTPException(409, "An analysis or VM operation is active")
    async with operation_lock:
        settings = get_settings()
        client = VirtualBoxClient()
        channel = guest_channel(settings)
        try:
            state = await client.get_state(settings.sandbox.vm_name)
            if state not in ("poweroff", "running"):
                raise HTTPException(409, f"VM state is {state}; normal shutdown is required before reset")
            if state == "running" and not await shutdown_normally(client, settings.sandbox.vm_name, channel=channel, timeout=120):
                raise HTTPException(409, "Normal shutdown did not complete. Baseline was not restored.")
            operation = await client.restore_snapshot(settings.sandbox.vm_name, settings.sandbox.snapshot_name)
            if not operation.success:
                raise HTTPException(500, operation.stderr)
            return {"status": "reset"}
        finally:
            await channel.aclose()


@router.get("/sessions/{session_id}/artifacts/{kind}")
async def artifact(session_id: str, kind: str):
    await metadata_for(session_id)
    if kind not in KINDS:
        raise HTTPException(400, "Unknown artifact kind")
    directory = await artifact_directory(session_id)
    if not directory:
        raise HTTPException(404, "No artifact location is available for this session")
    filename, media_type = KINDS[kind]
    path = directory / filename
    if not path.is_file() or not inside(path, directory):
        raise HTTPException(404, "Artifact not available")
    return FileResponse(path, media_type=media_type, filename=f"{session_id}-{filename}")


@router.get("/console/stream")
async def stream(request: Request, session_id: str | None = None):
    if session_id:
        valid_id(session_id)
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    listeners[queue] = session_id

    async def events():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            listeners.pop(queue, None)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
