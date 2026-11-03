import asyncio
import os
from datetime import datetime, timezone

os.environ["ADAM__SANDBOX__GUEST_USERNAME"] = "tester"
os.environ["ADAM__SANDBOX__GUEST_PASSWORD"] = "not-a-real-secret"

import pytest
from fastapi.testclient import TestClient

from adam.api.deps import deps
from adam.api.main import app, sessions_store
from adam.contracts.enums import Arm, NetworkMode, SessionStatus
from adam.contracts.session import AnalysisSession, SampleRef, SessionConfig, SessionMetrics


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_session(client):
    session_id = "test_dash_session"
    sample = SampleRef(
        sha256="a" * 64, md5="b" * 32, filename="test.exe", size_bytes=100, file_type="binary"
    )
    config = SessionConfig(
        deception_enabled=True, policy_ruleset="default", vm_profile="win10", timeout_seconds=300, network_mode=NetworkMode.SIMULATED
    )
    metadata = AnalysisSession(
        session_id=session_id, experiment_id="exp_dash", arm=Arm.TREATMENT, sample=sample,
        config=config, status=SessionStatus.COMPLETED, started_at=datetime.now(timezone.utc), metrics=SessionMetrics()
    )
    sessions_store[session_id] = {
        "metadata": metadata, "events": [], "decisions": [], "mutations": []
    }

    async def _seed():
        await deps.db_conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await deps.session_repo.create(metadata)
        await deps.db_conn.commit()

    asyncio.run(_seed())

    yield session_id
    if session_id in sessions_store:
        del sessions_store[session_id]

    async def _cleanup():
        await deps.db_conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await deps.db_conn.commit()

    asyncio.run(_cleanup())


@pytest.fixture
def seeded_comparison_experiment(client, mock_session):
    ctrl_id = "test_dash_session_ctrl"
    sample = SampleRef(
        sha256="c" * 64, md5="d" * 32, filename="test_ctrl.exe", size_bytes=100, file_type="binary"
    )
    config = SessionConfig(
        deception_enabled=False, policy_ruleset="default", vm_profile="win10", timeout_seconds=300, network_mode=NetworkMode.SIMULATED
    )
    metadata = AnalysisSession(
        session_id=ctrl_id, experiment_id="exp_dash", arm=Arm.CONTROL, sample=sample,
        config=config, status=SessionStatus.COMPLETED, started_at=datetime.now(timezone.utc), metrics=SessionMetrics()
    )
    sessions_store[ctrl_id] = {
        "metadata": metadata, "events": [], "decisions": [], "mutations": []
    }

    async def _seed():
        await deps.db_conn.execute("DELETE FROM sessions WHERE session_id = ?", (ctrl_id,))
        await deps.session_repo.create(metadata)
        await deps.db_conn.commit()

    asyncio.run(_seed())

    yield "exp_dash"
    if ctrl_id in sessions_store:
        del sessions_store[ctrl_id]

    async def _cleanup():
        await deps.db_conn.execute("DELETE FROM sessions WHERE session_id = ?", (ctrl_id,))
        await deps.db_conn.commit()

    asyncio.run(_cleanup())


def test_dashboard_index(client):
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_session_detail(client, mock_session):
    response = client.get(f"/dashboard/sessions/{mock_session}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_report(client, mock_session):
    response = client.get(f"/dashboard/sessions/{mock_session}/report")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    missing_response = client.get("/dashboard/sessions/nonexistent_session/report")
    assert missing_response.status_code == 404


def test_dashboard_comparison(client, seeded_comparison_experiment):
    response = client.get(f"/dashboard/experiments/{seeded_comparison_experiment}/comparison")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    missing_response = client.get("/dashboard/experiments/nonexistent_experiment/comparison")
    assert missing_response.status_code == 404
