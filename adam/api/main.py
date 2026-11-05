"""
adam/api/main.py
FastAPI backend for ADAM Live Pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import BackgroundTasks, FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from adam.contracts.enums import Arm, MutationStatus, NetworkMode, SessionStatus
from adam.contracts.envelope import Envelope
from adam.contracts.mutation import MutationResult
from adam.contracts.policy_decision import PolicyDecision
from adam.contracts.raw_event import RawEvent
from adam.contracts.semantic_event import SemanticEvent
from adam.contracts.session import (
    AnalysisSession,
    SampleRef,
    SessionConfig,
    SessionFailure,
    SessionMetrics,
)
from adam.fusion.log_generate import generate_attack_chain, generate_benign_events

if TYPE_CHECKING:
    from adam.pipeline.live import LiveOrchestrator

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RULES_PATH = BASE_DIR / "rules" / "default"

# In-memory store for sessions
sessions_store: dict[str, Any] = {}

# SSE Queues for connected clients
clients: list[asyncio.Queue] = []

orchestrator: LiveOrchestrator | None = None  # type: ignore

async def store_event(envelope: Envelope):
    sess_id = envelope.session_id
    if sess_id not in sessions_store:
        sessions_store[sess_id] = {"metadata": None, "events": [], "decisions": [], "mutations": []}
    
    msg_type = envelope.message_type
    payload_dict = envelope.payload.model_dump()
    payload_json = envelope.payload.model_dump_json()
    
    if msg_type == "SemanticEvent":
        sessions_store[sess_id]["events"].append(payload_dict)
    elif msg_type == "PolicyDecision":
        sessions_store[sess_id]["decisions"].append(payload_dict)
    elif msg_type == "MutationResult":
        sessions_store[sess_id]["mutations"].append(payload_dict)
        
    # Update metrics if metadata exists
    metadata = sessions_store[sess_id].get("metadata")
    if metadata:
        if msg_type == "SemanticEvent":
            metadata.metrics.semantic_events += 1
        elif msg_type == "PolicyDecision":
            metadata.metrics.decisions_total += 1
            is_deception_enabled = getattr(metadata.config, "deception_enabled", True) and getattr(metadata, "arm", None) != Arm.CONTROL
            if payload_dict.get("verdict") == "EXECUTE" and is_deception_enabled:
                metadata.metrics.decisions_executed += 1
        elif msg_type == "MutationResult":
            status = payload_dict.get("status")
            if status in ("APPLIED", MutationStatus.APPLIED, getattr(MutationStatus.APPLIED, "value", "APPLIED")):
                metadata.metrics.mutations_applied += 1
        
    from adam.api.console import broadcast
    broadcast(msg_type, payload_dict)

    # Broadcast to SSE
    sse_msg = f"event: {msg_type}\ndata: {payload_json}\n\n"
    for q in clients:
        if q.full():
            q.get_nowait()
        q.put_nowait(sse_msg)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # We initialize the dependencies from deps.py
    from adam.api.deps import deps, init_dependencies, shutdown_dependencies
    
    await init_dependencies()
    from adam.api.console import init_console
    await init_console()
    
    deps.bus.subscribe(SemanticEvent, store_event, name="api_events")
    deps.bus.subscribe(PolicyDecision, store_event, name="api_decisions")
    deps.bus.subscribe(MutationResult, store_event, name="api_mutations")
    
    try:
        yield
    finally:
        from adam.api.console import shutdown_jobs
        await shutdown_jobs()
        await shutdown_dependencies()

app = FastAPI(title="ADAM Live API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/v1/sessions")
async def get_sessions():
    from adam.api.deps import deps
    persisted = await deps.session_repo.list_all() if deps.session_repo else []
    return {"sessions": list(dict.fromkeys([session.session_id for session in persisted] + list(sessions_store)))}


async def _session_payload(session_id: str):
    """Serve durable Runner sessions as well as this process's live sessions."""
    from adam.api.deps import deps
    cached = sessions_store.get(session_id, {})
    metadata = await deps.session_repo.get_by_id(session_id) if deps.session_repo else None
    if metadata is None:
        return cached
    result = {"metadata": metadata}
    for key, repository, method, identifier in (
        ("events", deps.event_repo, "get_semantic_by_session", "semantic_id"),
        ("decisions", deps.decision_repo, "get_by_session", "decision_id"),
        ("mutations", deps.mutation_repo, "get_by_session", "mutation_id"),
    ):
        records = await getattr(repository, method)(session_id) if repository else []
        merged = {getattr(record, identifier): record.model_dump(mode="json") for record in records}
        if metadata.status == SessionStatus.RUNNING:
            for record in cached.get(key, []):
                merged.setdefault(record[identifier], record)
        result[key] = list(merged.values())
    return result

@app.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str):
    return await _session_payload(session_id)

@app.get("/api/v1/sessions/{session_id}/events")
async def get_session_events(session_id: str):
    return (await _session_payload(session_id)).get("events", [])

@app.get("/api/v1/sessions/{session_id}/decisions")
async def get_session_decisions(session_id: str):
    return (await _session_payload(session_id)).get("decisions", [])

@app.get("/api/v1/sessions/{session_id}/mutations")
async def get_session_mutations(session_id: str):
    return (await _session_payload(session_id)).get("mutations", [])

from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse


@app.get("/api/v1/sessions/{session_id}/report")
async def get_session_report(session_id: str, format: str = "json"):
    from adam.api.deps import deps
    if not deps.report_generator:
        return JSONResponse(content={"error": "Report generator not initialized"}, status_code=500)
    try:
        report = await deps.report_generator.generate(session_id, format)
        if format == "html":
            return HTMLResponse(content=report)
        elif format == "md":
            return PlainTextResponse(content=report, media_type="text/markdown")
        else:
            return JSONResponse(content=json.loads(report))
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=404)
    except Exception as e:
        logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/v1/experiments/{experiment_id}/comparison")
async def get_experiment_comparison(experiment_id: str):
    from adam.api.deps import deps
    if not deps.report_generator:
        return JSONResponse(content={"error": "Report generator not initialized"}, status_code=500)
    try:
        report = await deps.report_generator.generate_comparison(experiment_id)
        return JSONResponse(content=json.loads(report))
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=404)
    except Exception as e:
        logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
        return JSONResponse(content={"error": str(e)}, status_code=500)

async def run_deterministic_simulation(session_id: str, seed: int):
    import uuid

    from adam.api.deps import deps
    metadata: AnalysisSession = sessions_store[session_id]["metadata"]
    
    # 1. Generate events deterministically
    random.seed(seed)
    start_time = datetime(2026, 8, 5, 9, 0, 0, tzinfo=UTC)
    attack_events = []
    attack_events.extend(generate_attack_chain("WKSTN-666", "j.smith", start_time))
    benign_events = generate_benign_events(200, start_time)
    all_events = benign_events + attack_events
    all_events.sort(key=lambda x: datetime.fromisoformat(x["timestamp"]))
    
    metadata.metrics.raw_events = len(all_events)
    
    # 2. Publish as Envelope[RawEvent]
    for ev in all_events:
        try:
            ts = datetime.fromisoformat(ev["timestamp"])
        except Exception:
            logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
            ts = datetime.now(UTC)
            
        from adam.contracts.raw_event import ProcessInfo
        
        proc = ProcessInfo(
            pid=ev.get("pid", 1000),
            ppid=ev.get("ppid", 1000),
            image=ev.get("image_path", f"C:\\Windows\\System32\\{ev.get('process_name', 'unknown.exe')}"),
            command_line=ev.get("command_line", ""),
            integrity_level="High",
            user=ev.get("user", "System"),
            guid=f"{{guid-{uuid.uuid4().hex[:8]}}}"
        )
        
        raw_event = RawEvent(
            event_id=f"raw_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            source="SYSMON",
            source_event_id=1,
            category="PROCESS",
            occurred_at=ts,
            observed_at=datetime.now(UTC),
            process=proc,
            attributes=ev
        )
        
        env = Envelope[RawEvent](
            envelope_version="1.0",
            message_id=str(uuid.uuid4()),
            message_type="RawEvent",
            session_id=session_id,
            correlation_id=f"corr_{uuid.uuid4().hex[:8]}",
            emitted_at=datetime.now(UTC),
            emitter="sim",
            payload=raw_event
        )
        await deps.bus.publish(env)
        await asyncio.sleep(0.005) # space out events
                
    # Completion follows actual downstream processing and durable writes.
    await deps.bus.wait_idle(timeout=60)
    for _ in range(3):
        if not deps.db_writer.queue:
            break
        await deps.db_writer.flush()
    if deps.db_writer.queue:
        raise RuntimeError("Simulation persistence did not complete after three flush attempts")

    # Mark as completed
    metadata.status = SessionStatus.COMPLETED
    metadata.ended_at = datetime.now(UTC)
    await deps.session_repo.update(metadata)
    await deps.db_conn.commit()
    
    from adam.api.console import broadcast
    broadcast("SessionCompleted", {"session_id": session_id, "status": metadata.status.value})
    await deps.bus.publish(Envelope[dict](
        envelope_version="1.0",
        message_id=str(uuid.uuid4()),
        message_type="SessionCompleted",
        session_id=session_id,
        correlation_id=session_id,
        emitted_at=datetime.now(UTC),
        emitter="Orchestrator",
        payload={}
    ))


@app.post("/api/v1/sessions/simulate")
async def simulate_session(
    background_tasks: BackgroundTasks, 
    file: Annotated[UploadFile, File()],
    deception_enabled: bool = True,
    experiment_id: str | None = None,
):
    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()
    md5 = hashlib.md5(content).hexdigest()
    
    seed = int(sha256[:16], 16)
    import uuid
    session_id = f"sim_{uuid.uuid4().hex[:8]}"
    
    sample = SampleRef(
        sha256=sha256,
        md5=md5,
        filename=file.filename or "unknown",
        size_bytes=len(content),
        file_type="binary"
    )
    config = SessionConfig(
        deception_enabled=deception_enabled,
        policy_ruleset="default",
        vm_profile="windows-10",
        timeout_seconds=300,
        network_mode=NetworkMode.SIMULATED
    )
    metadata = AnalysisSession(
        session_id=session_id,
        experiment_id=experiment_id or "exp_demo_01",
        arm=Arm.TREATMENT if deception_enabled else Arm.CONTROL,
        sample=sample,
        config=config,
        status=SessionStatus.RUNNING,
        started_at=datetime.now(UTC),
        metrics=SessionMetrics()
    )
    
    sessions_store[session_id] = {
        "metadata": metadata,
        "events": [],
        "decisions": [],
        "mutations": []
    }
    

    from adam.api.deps import deps
    
    async def create_and_run():
        await deps.session_repo.create(metadata)
        await deps.db_conn.commit()
        try:
            await run_deterministic_simulation(session_id, seed)
        except Exception as exc:
            logging.getLogger(__name__).exception("Simulation failed: %s", session_id)
            metadata.status = SessionStatus.FAILED
            metadata.error = str(exc)
            metadata.ended_at = datetime.now(UTC)
            metadata.failures.append(SessionFailure(stage="simulation", code=type(exc).__name__, description=str(exc)))
            await deps.session_repo.update(metadata)
            await deps.db_conn.commit()
            from adam.api.console import broadcast
            broadcast("SessionCompleted", {"session_id": session_id, "status": "FAILED"})

    background_tasks.add_task(create_and_run)
    return {"session_id": session_id, "status": "RUNNING"}

@app.post("/api/v1/experiments/run")
async def run_experiment(background_tasks: BackgroundTasks, file: Annotated[UploadFile, File()]):
    # Read file once
    content = await file.read()
    import uuid
    experiment_id = f"exp_{uuid.uuid4().hex[:8]}"
    
    # We will simulate the CONTROL session
    class MockFile:
        def __init__(self, filename, content):
            self.filename = filename
            self.content = content
        async def read(self):
            return self.content
            
    mock_file = MockFile(file.filename, content)
    
    # Create CONTROL session
    ctrl_res = await simulate_session(
        background_tasks, mock_file, deception_enabled=False, experiment_id=experiment_id
    )
    
    # Create TREATMENT session
    trt_res = await simulate_session(
        background_tasks, mock_file, deception_enabled=True, experiment_id=experiment_id
    )
    
    return {
        "experiment_id": experiment_id,
        "control_session": ctrl_res["session_id"],
        "treatment_session": trt_res["session_id"]
    }

@app.get("/api/v1/stream")
async def sse_stream(request: Request):
    q = asyncio.Queue(maxsize=256)
    clients.append(q)
    
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await q.get()
                yield msg
        finally:
            if q in clients:
                clients.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ── Minimal API Glue for Frontend ──────────────────────────────────────────

@app.post("/api/v1/sessions/replay")
async def replay_session():
    """Run the synthetic replay via adam.cli.main replay synthetic."""
    import re
    import subprocess
    import sys

    res = await asyncio.to_thread(
        lambda: subprocess.run(
            [sys.executable, "-m", "adam.cli.main", "replay", "synthetic"],
            capture_output=True,
            text=True,
            check=False,
        )
    )
    match = re.search(r"Session ID: (replay_\w+)", res.stdout)
    if match and res.returncode == 0:
        return {"session_id": match.group(1), "status": "COMPLETED"}
    return JSONResponse(
        {"error": "Replay failed", "stderr": res.stderr[:2000], "stdout": res.stdout[:2000]},
        status_code=500,
    )


from adam.api.console import router as console_router

app.include_router(console_router)

from adam.dashboard.router import dashboard_router

app.include_router(dashboard_router)
app.mount("/dashboard/static", StaticFiles(directory=Path(__file__).parent.parent / "dashboard" / "static"), name="static")
