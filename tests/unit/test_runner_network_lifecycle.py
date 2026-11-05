"""Runner lifecycle tests for the bounded network interceptor (Phase 2.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import aiosqlite
import pytest

from adam.common.config import DatabaseSettings, SandboxSettings, Settings
from adam.contracts.enums import Arm, NetworkMode, SessionStatus
from adam.contracts.session import AnalysisSession, SampleRef, SessionConfig, SessionMetrics
from adam.orchestrator import runner as runner_module
from adam.orchestrator.runner import GuestRuntime, Runner
from adam.pipeline.wiring import EngineHandles

SAMPLE = SampleRef(
    sha256="a" * 64,
    md5="b" * 32,
    filename="sample.exe",
    size_bytes=1,
    file_type="PE32",
)


def _settings(tmp_path) -> Settings:
    return Settings(
        sandbox=SandboxSettings(
            vm_name="adam-test-vm",
            guest_username="test",
            guest_password="test",
        ),
        db=DatabaseSettings(path=str(tmp_path / "adam.db")),
    )


class FakeInterceptor:
    def __init__(self) -> None:
        self.is_active = False
        self.start_count = 0
        self.stop_count = 0
        self.host = "127.0.0.1"
        self.bound_port = 12345
        self.proxy_url = "http://127.0.0.1:12345"

    async def start(self) -> None:
        self.start_count += 1
        self.is_active = True

    async def stop(self) -> None:
        self.stop_count += 1
        self.is_active = False


class FakeChannel:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeController:
    def __init__(self, *args, **kwargs) -> None:
        self.vm_profile = None


class FakeOrchestrator:
    fail = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run_session(self, sample, settings, **kwargs):
        if self.fail:
            raise RuntimeError("simulated session failure")
        now = datetime.now(UTC)
        return AnalysisSession(
            session_id=kwargs["session_id"],
            experiment_id=kwargs["experiment_id"],
            arm=kwargs["arm"],
            sample=sample,
            config=SessionConfig(
                deception_enabled=kwargs["deception_enabled"],
                policy_ruleset="default",
                vm_profile="adam-test-vm",
                timeout_seconds=300,
                network_mode=NetworkMode.HOST_ONLY,
            ),
            status=SessionStatus.COMPLETED,
            started_at=now,
            ended_at=now,
            metrics=SessionMetrics(),
        )


def _patch_runner(monkeypatch, runtime: GuestRuntime) -> None:
    monkeypatch.setattr(
        runner_module,
        "sample_ref_from_path",
        lambda path: SAMPLE,
    )
    monkeypatch.setattr(
        runner_module,
        "new_session_id",
        lambda: "sess_phase2",
    )
    monkeypatch.setattr(
        Runner,
        "_build_collectors",
        lambda self, *args, **kwargs: [],
    )
    monkeypatch.setattr(
        runner_module,
        "VirtualBoxClient",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        Runner,
        "_build_guest_channel",
        lambda self, client, settings, **kwargs: runtime,
    )
    monkeypatch.setattr(
        runner_module,
        "SandboxController",
        FakeController,
    )
    monkeypatch.setattr(
        runner_module,
        "wire_engines",
        lambda *args, **kwargs: EngineHandles(),
    )
    monkeypatch.setattr(
        runner_module,
        "SessionOrchestrator",
        FakeOrchestrator,
    )


@pytest.mark.asyncio
async def test_runner_starts_and_stops_interceptor(monkeypatch, tmp_path) -> None:
    interceptor = FakeInterceptor()
    channel = FakeChannel()
    _patch_runner(monkeypatch, GuestRuntime(channel=channel, interceptor=interceptor))
    runner = Runner(settings=_settings(tmp_path))

    session = await runner.run("sample.exe", arm=Arm.TREATMENT)

    assert session.arm is Arm.TREATMENT
    assert interceptor.start_count == 1
    assert interceptor.stop_count == 1
    assert interceptor.is_active is False
    assert channel.closed is True


@pytest.mark.asyncio
async def test_runner_does_not_start_interceptor_for_control(monkeypatch, tmp_path) -> None:
    interceptor = FakeInterceptor()
    channel = FakeChannel()
    _patch_runner(monkeypatch, GuestRuntime(channel=channel, interceptor=interceptor))
    runner = Runner(settings=_settings(tmp_path))

    session = await runner.run("sample.exe", arm=Arm.CONTROL)

    assert session.arm is Arm.CONTROL
    assert interceptor.start_count == 0
    assert interceptor.stop_count == 0
    assert channel.closed is True


@pytest.mark.asyncio
async def test_runner_stops_interceptor_on_session_failure(monkeypatch, tmp_path) -> None:
    interceptor = FakeInterceptor()
    channel = FakeChannel()
    _patch_runner(monkeypatch, GuestRuntime(channel=channel, interceptor=interceptor))
    FakeOrchestrator.fail = True
    try:
        runner = Runner(settings=_settings(tmp_path))
        with pytest.raises(RuntimeError, match="simulated session failure"):
            await runner.run("sample.exe", arm=Arm.TREATMENT)
    finally:
        FakeOrchestrator.fail = False

    assert interceptor.start_count == 1
    assert interceptor.stop_count == 1
    assert interceptor.is_active is False
    assert channel.closed is True


@pytest.mark.asyncio
async def test_runner_without_interceptor_is_unchanged(monkeypatch, tmp_path) -> None:
    channel = FakeChannel()
    _patch_runner(monkeypatch, GuestRuntime(channel=channel, interceptor=None))
    runner = Runner(settings=_settings(tmp_path))

    session = await runner.run("sample.exe", arm=Arm.TREATMENT)

    assert session.arm is Arm.TREATMENT
    assert channel.closed is True


@pytest.mark.asyncio
async def test_final_session_experiment_id_matches_database_and_artifact(monkeypatch, tmp_path) -> None:
    import json

    from adam.db.schema import SCHEMA_SQL

    settings = _settings(tmp_path)
    async with aiosqlite.connect(settings.db.path) as db:
        await db.executescript(SCHEMA_SQL)
    _patch_runner(monkeypatch, GuestRuntime(channel=FakeChannel(), interceptor=None))
    session = await Runner(settings=settings).run('sample.exe', arm=Arm.CONTROL, artifacts_dir=str(tmp_path))
    artifact = json.loads((tmp_path / session.session_id / 'session.json').read_text())
    async with (aiosqlite.connect(settings.db.path) as db,
                db.execute('SELECT payload FROM sessions WHERE session_id=?', (session.session_id,)) as cursor):
        stored = json.loads((await cursor.fetchone())[0])
    assert session.experiment_id == 'exp_sess_phase2'
    assert stored == artifact == session.model_dump(mode='json')
