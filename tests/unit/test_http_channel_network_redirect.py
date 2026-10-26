"""Guest proxy/hosts redirect tests for HTTPGuestChannel."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.vbox.models import VMOperationResult


def _ok() -> VMOperationResult:
    return VMOperationResult(
        success=True,
        command=("http_agent", "/process/start", "powershell.exe"),
        duration_ms=1.0,
        return_code=0,
        stdout="",
        stderr="",
    )


def _decode_script(channel: HTTPGuestChannel) -> str:
    args = channel.run_process.call_args.args[1]  # type: ignore[attr-defined]
    encoded = args[args.index("-EncodedCommand") + 1]
    return base64.b64decode(encoded).decode("utf-16-le")


class FakeInterceptor:
    is_active = True
    proxy_url = "http://192.168.56.1:8080"

    def __init__(self) -> None:
        self.rules: dict[str, str | None] = {}
        self.shares: dict[str, str | None] = {}
        self.respond = AsyncMock(side_effect=self._respond)
        self.unrespond = AsyncMock(side_effect=self._unrespond)
        self.mount_share = AsyncMock()
        self.unmount_share = AsyncMock()

    async def _respond(self, target: str, value: str | None) -> None:
        self.rules[target] = value

    async def _unrespond(self, target: str) -> None:
        self.rules.pop(target, None)


def _channel(
    interceptor: FakeInterceptor | None = None,
    *,
    hosts_redirect_enabled: bool = False,
) -> HTTPGuestChannel:
    return HTTPGuestChannel(
        "http://127.0.0.1:8765",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path=None,
        tshark_path=None,
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
        network_interceptor=interceptor,
        network_redirect_enabled=True,
        hosts_redirect_enabled=hosts_redirect_enabled,
        proxy_bypass="localhost;127.0.0.1;<local>",
        retry_attempts=1,
    )


@pytest.mark.asyncio
async def test_configure_http_proxy_uses_encoded_command_and_expected_values() -> None:
    channel = _channel()
    channel.run_process = AsyncMock(return_value=_ok())  # type: ignore[method-assign]

    await channel.configure_http_proxy("http://192.168.56.1:8080")

    script = _decode_script(channel)
    assert "ProxyEnable" in script
    assert "ProxyServer" in script
    assert "netsh.exe winhttp set proxy" in script
    assert "http://192.168.56.1:8080" in script
    assert "192.168.56.1:8080" in script
    assert "localhost;127.0.0.1;<local>" in script
    assert channel.network_configured is True


@pytest.mark.asyncio
async def test_reset_http_proxy_disables_and_resets() -> None:
    channel = _channel()
    channel.run_process = AsyncMock(return_value=_ok())  # type: ignore[method-assign]
    channel._proxy_configured = True
    channel._network_configured = True

    await channel.reset_http_proxy()

    script = _decode_script(channel)
    assert "ProxyEnable" in script
    assert "-Value 0" in script
    assert "netsh.exe winhttp reset proxy" in script
    assert channel.network_configured is False


@pytest.mark.asyncio
async def test_hosts_entry_is_quote_safe_for_apostrophe() -> None:
    channel = _channel()
    channel.run_process = AsyncMock(return_value=_ok())  # type: ignore[method-assign]

    await channel.add_hosts_entry("evil'host", "192.168.56.1")

    script = _decode_script(channel)
    assert "evil''host" in script
    assert "192.168.56.1" in script
    assert "# ADAM-LURE" in script
    assert channel._hosts_entries == {"evil'host": "192.168.56.1"}

    await channel.remove_hosts_entry("evil'host")
    script = _decode_script(channel)
    assert "evil''host" in script
    assert channel._hosts_entries == {}


@pytest.mark.asyncio
async def test_hosts_entry_rejects_whitespace() -> None:
    channel = _channel()
    channel.run_process = AsyncMock(return_value=_ok())  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="invalid hostname"):
        await channel.add_hosts_entry("evil host", "192.168.56.1")
    channel.run_process.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_respond_configures_proxy_once_across_mutations() -> None:
    interceptor = FakeInterceptor()
    channel = _channel(interceptor)
    calls: list[str] = []

    async def fake_configure(*args: object, **kwargs: object) -> None:
        calls.append("configure")
        channel._network_configured = True

    channel.configure_http_proxy = fake_configure  # type: ignore[method-assign]

    await channel.apply_mutation("NETWORK", "http://c2.one/beacon", "RESPOND", "one")
    await channel.apply_mutation("NETWORK", "http://c2.two/beacon", "RESPOND", "two")

    assert calls == ["configure"]
    assert interceptor.respond.await_count == 2


@pytest.mark.asyncio
async def test_unrespond_last_rule_resets_redirect() -> None:
    interceptor = FakeInterceptor()
    channel = _channel(interceptor)
    channel._network_configured = True
    channel._proxy_configured = True
    channel.reset_http_proxy = AsyncMock()  # type: ignore[method-assign]

    await channel.apply_mutation("NETWORK", "http://c2.one/beacon", "RESPOND", "one")
    await channel.apply_mutation("NETWORK", "http://c2.one/beacon", "UNRESPOND", None)

    channel.reset_http_proxy.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_reset_network_redirect_removes_hosts_and_proxy() -> None:
    channel = _channel()
    channel._hosts_entries = {"c2.test": "192.168.56.1"}
    channel._proxy_configured = True
    channel._network_configured = True
    channel.remove_hosts_entry = AsyncMock()  # type: ignore[method-assign]
    channel.reset_http_proxy = AsyncMock()  # type: ignore[method-assign]

    await channel.reset_network_redirect()

    channel.remove_hosts_entry.assert_awaited_once_with("c2.test")  # type: ignore[attr-defined]
    channel.reset_http_proxy.assert_awaited_once()  # type: ignore[attr-defined]
    assert channel.network_configured is False
