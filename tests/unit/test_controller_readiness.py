"""Regression tests for backend-agnostic readiness and persona application."""

from __future__ import annotations

import time

import pytest

from adam.sandbox.controller import SandboxController
from adam.sandbox.guest.agent.agent import TelemetryArtifacts, ToolAvailability
from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.vbox.models import VMOperationResult


def _ok() -> VMOperationResult:
    return VMOperationResult(
        success=True,
        command=("VBoxManage", "mock"),
        duration_ms=1.0,
        return_code=0,
        stdout="",
        stderr="",
    )


class FakeClient:
    async def get_state(self, vm_name: str) -> str:
        return "poweroff"

    async def stop(self, vm_name: str, *, mode: str = "poweroff") -> VMOperationResult:
        return _ok()

    async def discard_saved_state(self, vm_name: str) -> VMOperationResult:
        return _ok()

    async def restore_snapshot(self, vm_name: str, snapshot: str) -> VMOperationResult:
        return _ok()

    async def start(self, vm_name: str, *, headless: bool = True) -> VMOperationResult:
        return _ok()

    async def wait_for_state(self, vm_name: str, state: str, *, timeout: float) -> VMOperationResult:
        return _ok()

    async def wait_for_guest_ready(
        self,
        vm_name: str,
        *,
        guest_username: str,
        guest_password: str,
        timeout: float,
    ) -> VMOperationResult:
        return _ok()


class HttpLikeChannel:
    def __init__(self) -> None:
        self.ready_called = False
        self.timeout_seen: float | None = None
        self.verify_called = False

    async def wait_until_ready(self, timeout_s: float | None = None) -> None:
        self.ready_called = True
        self.timeout_seen = timeout_s

    async def verify_tools(self) -> ToolAvailability:
        self.verify_called = True
        return ToolAvailability(True, True, True, {})

    async def start_captures(self, session_id: str, **kwargs: object) -> None:
        pass

    async def stop_export_and_fetch(self, session_id: str, host_artifact_dir: str, **kwargs: object) -> TelemetryArtifacts:
        return TelemetryArtifacts()


class LegacyChannel:
    def __init__(self) -> None:
        self.ready_called = False
        self.verify_called = False

    async def wait_until_ready(self) -> None:
        self.ready_called = True

    async def verify_tools(self) -> ToolAvailability:
        self.verify_called = True
        return ToolAvailability(True, True, True, {})

    async def start_captures(self, session_id: str, **kwargs: object) -> None:
        pass

    async def stop_export_and_fetch(self, session_id: str, host_artifact_dir: str, **kwargs: object) -> TelemetryArtifacts:
        return TelemetryArtifacts()


def _controller(channel: object) -> SandboxController:
    return SandboxController(
        client=FakeClient(),  # type: ignore[arg-type]
        vm_name="TEST_VM",
        guest_username="tester",
        guest_password="not-a-real-secret",
        guest_channel=channel,  # type: ignore[arg-type]
        guest_ready_timeout=12.5,
    )


@pytest.mark.asyncio
async def test_http_like_channel_receives_timeout_and_verifies_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("adam.sandbox.controller.asyncio.sleep", _no_sleep)
    channel = HttpLikeChannel()
    controller = _controller(channel)

    await controller.prepare()

    assert channel.ready_called is True
    assert channel.timeout_seen == 12.5
    assert channel.verify_called is True


@pytest.mark.asyncio
async def test_legacy_channel_without_timeout_kwarg_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("adam.sandbox.controller.asyncio.sleep", _no_sleep)
    channel = LegacyChannel()
    controller = _controller(channel)

    await controller.prepare()

    assert channel.ready_called is True
    assert channel.verify_called is True


@pytest.mark.asyncio
async def test_persona_is_applied_for_http_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("adam.sandbox.controller.asyncio.sleep", _no_sleep)
    applied: list[tuple[object, object]] = []

    class FakeProfile:
        pass

    def fake_load_profile(name: str) -> FakeProfile:
        assert name == "win10_x64_developer_decoy"
        return FakeProfile()

    async def fake_apply_persona(channel: object, profile: object) -> None:
        applied.append((channel, profile))

    monkeypatch.setattr("adam.sandbox.vbox.profile_applier.load_profile", fake_load_profile)
    monkeypatch.setattr("adam.sandbox.vbox.profile_applier.apply_profile_persona", fake_apply_persona)

    channel = HttpLikeChannel()
    controller = _controller(channel)
    controller.vm_profile = "developer_decoy"

    await controller.prepare()

    assert len(applied) == 1
    assert applied[0][0] is channel


@pytest.mark.asyncio
async def test_http_wait_until_ready_has_no_fixed_sleep() -> None:
    channel = HTTPGuestChannel(
        "http://127.0.0.1:1",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path=None,
        tshark_path=None,
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
        request_timeout_s=1.0,
        retry_attempts=1,
    )

    async def healthy() -> tuple[bool, str]:
        return True, "ok"

    channel._probe_health_once = healthy  # type: ignore[method-assign]
    started = time.monotonic()
    await channel.wait_until_ready(timeout_s=1.0)
    elapsed = time.monotonic() - started

    assert channel._ready is True
    assert elapsed < 0.5


async def _no_sleep(_seconds: float) -> None:
    return None
