"""Phase 3 strict guest-tool gate tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from adam.common.bus import EventBus
from adam.common.config import LiveSettings, Settings
from adam.contracts.enums import SessionStatus
from adam.contracts.session import SampleRef
from adam.orchestrator.session import SessionOrchestrator
from adam.sandbox.guest.agent.agent import ToolAvailability, required_missing_tools
from adam.sandbox.guest.channel import TelemetryArtifacts
from adam.sandbox.guest.http_channel import HTTPGuestChannel


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        sandbox={
            "vm_name": "TEST_VM",
            "guest_username": "tester",
            "guest_password": "not-a-real-secret",
        },
        live=LiveSettings(enabled=False),
    )


def _sample() -> SampleRef:
    return SampleRef(
        sha256="0" * 64,
        md5="0" * 32,
        filename="sample.exe",
        size_bytes=1,
        file_type="PE32",
    )


class FakeController:
    vm_profile = None

    def __init__(self) -> None:
        self.prepare_called = False
        self.arm_called = False
        self.detonate_called = False

    async def prepare(self) -> None:
        self.prepare_called = True

    async def arm(self, host_sample_path: str, guest_target_path: str, *, timeout: float = 60.0) -> None:
        self.arm_called = True

    async def detonate(self, sample: SampleRef) -> None:
        self.detonate_called = True

    async def teardown(self) -> None:
        pass


class FakeGuest:
    def __init__(self, availability: ToolAvailability) -> None:
        self._availability = availability
        self.start_captures_called = False

    async def verify_tools(self) -> ToolAvailability:
        return self._availability

    async def start_captures(self, session_id: str, *, capture_procmon: bool = True, capture_network: bool = True) -> None:
        self.start_captures_called = True

    async def stop_export_and_fetch(self, session_id: str, host_artifact_dir: str | Path, **kwargs) -> TelemetryArtifacts:
        return TelemetryArtifacts()


async def _run(tmp_path: Path, availability: ToolAvailability):
    controller = FakeController()
    guest = FakeGuest(availability)
    orchestrator = SessionOrchestrator(
        controller,  # type: ignore[arg-type]
        EventBus(),
        [],
        artifacts_dir=tmp_path,
        bus_drain_timeout=1.0,
        post_detonation_drain_seconds=0.0,
        guest_agent=guest,  # type: ignore[arg-type]
    )
    session = await orchestrator.run_session(
        _sample(),
        _settings(tmp_path),
        host_sample_path="sample.exe",
    )
    return session, controller, guest


def test_tool_availability_required_missing_default_is_empty() -> None:
    report = ToolAvailability(True, True, True, {})
    assert report.required_missing == ()


def test_required_missing_is_stable_and_deduplicated() -> None:
    assert required_missing_tools(
        procmon_available=False,
        tshark_available=False,
        sysmon_log_available=False,
        tools_required=True,
    ) == ("procmon", "tshark", "sysmon")
    assert required_missing_tools(
        procmon_available=False,
        tshark_available=True,
        sysmon_log_available=False,
        tools_required=True,
    ) == ("procmon", "sysmon")
    assert required_missing_tools(
        procmon_available=False,
        tshark_available=False,
        sysmon_log_available=False,
        tools_required=False,
    ) == ()


@pytest.mark.asyncio
async def test_tools_required_missing_procmon_raises_before_arm(tmp_path: Path) -> None:
    session, controller, guest = await _run(
        tmp_path,
        ToolAvailability(False, True, True, {"procmon": "missing"}, ("procmon",)),
    )
    assert session.status is SessionStatus.FAILED
    assert "Required guest tools are unavailable: procmon" in (session.error or "")
    assert controller.prepare_called is True
    assert controller.arm_called is False
    assert controller.detonate_called is False
    assert guest.start_captures_called is False


@pytest.mark.asyncio
async def test_tools_required_false_missing_procmon_continues(tmp_path: Path) -> None:
    session, controller, guest = await _run(
        tmp_path,
        ToolAvailability(False, True, True, {"procmon": "missing"}),
    )
    assert session.status is SessionStatus.COMPLETED
    assert controller.arm_called is True
    assert controller.detonate_called is True
    assert guest.start_captures_called is True


@pytest.mark.asyncio
async def test_tools_required_all_present_continues(tmp_path: Path) -> None:
    session, controller, guest = await _run(
        tmp_path,
        ToolAvailability(True, True, True, {}),
    )
    assert session.status is SessionStatus.COMPLETED
    assert controller.arm_called is True
    assert controller.detonate_called is True
    assert guest.start_captures_called is True


def _envelope(data: dict | None = None, *, success: bool = True) -> httpx.Response:
    body = {"success": success, "error_code": None, "error_message": None, "data": data}
    return httpx.Response(200 if success else 500, json=body)


def _http_channel(*, procmon_present: bool, tools_required: bool) -> HTTPGuestChannel:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path == "/health":
            return _envelope({"status": "ok", "uptime_s": 1.0})
        if path == "/filesystem/exists":
            target = params.get("path", "")
            present = procmon_present if "Procmon" in target else True
            return _envelope({"exists": present, "is_directory": False, "size_bytes": None})
        if path == "/sysmon/diagnostics":
            return _envelope({"channel_available": True, "event_count": 1})
        raise AssertionError(f"unhandled {request.method} {path}")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://fake-guest:8765",
    )
    return HTTPGuestChannel(
        "http://fake-guest:8765",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path="C:\\Procmon64.exe",
        tshark_path="C:\\tshark.exe",
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
        client=client,
        retry_attempts=1,
        guest_ready_timeout_s=1.0,
        readiness_poll_interval_s=0.01,
        tools_required=tools_required,
    )


@pytest.mark.asyncio
async def test_http_channel_required_missing_when_strict() -> None:
    channel = _http_channel(procmon_present=False, tools_required=True)
    report = await channel.verify_tools()
    assert report.procmon_available is False
    assert report.required_missing == ("procmon",)
    await channel.aclose()


@pytest.mark.asyncio
async def test_http_channel_graceful_when_not_strict() -> None:
    channel = _http_channel(procmon_present=False, tools_required=False)
    report = await channel.verify_tools()
    assert report.procmon_available is False
    assert report.required_missing == ()
    await channel.aclose()


@pytest.mark.asyncio
async def test_http_channel_all_present_when_strict() -> None:
    channel = _http_channel(procmon_present=True, tools_required=True)
    report = await channel.verify_tools()
    assert report.procmon_available is True
    assert report.required_missing == ()
    await channel.aclose()


@pytest.mark.asyncio
async def test_http_channel_rejects_invalid_sysmon_event_ids() -> None:
    channel = _http_channel(procmon_present=True, tools_required=False)
    with pytest.raises(ValueError, match="comma-separated integers"):
        await channel.get_sysmon_events_since(event_ids="1,3;2")
    await channel.aclose()
