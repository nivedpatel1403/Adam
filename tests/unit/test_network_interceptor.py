"""
tests/unit/test_network_interceptor.py

Unit tests for host-side NetworkInterceptor and its integration with HTTPGuestChannel
for NETWORK kind mutations (FABRICATE_C2_RESPONSE, MOUNT_FAKE_NETWORK_SHARE).
"""

from __future__ import annotations

import pytest

from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.network.interceptor import NetworkInterceptor


@pytest.mark.asyncio
async def test_network_interceptor_respond_and_match() -> None:
    interceptor = NetworkInterceptor()
    await interceptor.start()
    assert interceptor.is_active is True

    c2_url = "http://c2.malicious.org/api/v1/beacon"
    c2_resp = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-C2-Id: 999\r\n\r\n{\"cmd\": \"sleep 60\"}"

    await interceptor.respond(c2_url, c2_resp)

    assert c2_url in interceptor.rules
    rule = interceptor.rules[c2_url]
    assert rule.status_code == 200
    assert rule.headers.get("content-type") == "application/json"
    assert rule.headers.get("x-c2-id") == "999"
    assert rule.body == b"{\"cmd\": \"sleep 60\"}"

    # Test match_response
    matched = interceptor.match_response(c2_url)
    assert matched is not None
    assert matched.status_code == 200

    # Test revert / unrespond
    await interceptor.unrespond(c2_url)
    assert c2_url not in interceptor.rules
    assert interceptor.match_response(c2_url) is None

    await interceptor.stop()
    assert interceptor.is_active is False


@pytest.mark.asyncio
async def test_network_interceptor_mount_and_unmount_share() -> None:
    async with NetworkInterceptor() as interceptor:
        share_target = r"\\CorpFS01\Confidential"
        share_name = "Confidential_Decoy"

        await interceptor.mount_share(share_target, share_name)
        assert interceptor.shares[share_target] == share_name

        await interceptor.unmount_share(share_target)
        assert share_target not in interceptor.shares


@pytest.mark.asyncio
async def test_http_channel_delegates_network_mutations_to_interceptor() -> None:
    interceptor = NetworkInterceptor()
    channel = HTTPGuestChannel(
        "http://127.0.0.1:8765",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path=None,
        tshark_path=None,
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
        network_interceptor=interceptor,
    )

    c2_url = "http://c2.evil.com/beacon"
    c2_val = "HTTP/1.1 200 OK\r\n\r\nbeacon_ack"

    # 1. Test RESPOND
    await channel.apply_mutation("NETWORK", c2_url, "RESPOND", c2_val)
    assert c2_url in interceptor.rules
    assert interceptor.rules[c2_url].body == b"beacon_ack"

    # 2. Test UNRESPOND
    await channel.apply_mutation("NETWORK", c2_url, "UNRESPOND", None)
    assert c2_url not in interceptor.rules

    # 3. Test MOUNT
    share = r"\\Files\Finance"
    await channel.apply_mutation("NETWORK", share, "MOUNT", "FinanceShare")
    assert interceptor.shares[share] == "FinanceShare"

    # 4. Test UNMOUNT
    await channel.apply_mutation("NETWORK", share, "UNMOUNT", None)
    assert share not in interceptor.shares

    # 5. Unsupported operation raises NotImplementedError
    with pytest.raises(NotImplementedError, match="unsupported NETWORK operation"):
        await channel.apply_mutation("NETWORK", share, "INVALID_OP", None)


@pytest.mark.asyncio
async def test_http_channel_without_interceptor_raises_not_implemented() -> None:
    channel = HTTPGuestChannel(
        "http://127.0.0.1:8765",
        capture_dir="C:\\ADAM\\telemetry",
        procmon_path=None,
        tshark_path=None,
        sysmon_log="Microsoft-Windows-Sysmon/Operational",
        network_interceptor=None,
    )

    with pytest.raises(NotImplementedError, match="NETWORK kind operations require a NetworkInterceptor"):
        await channel.apply_mutation("NETWORK", "http://c2.bad.org", "RESPOND", "data")
