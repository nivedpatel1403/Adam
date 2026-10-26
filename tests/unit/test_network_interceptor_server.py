"""Real-server tests for the bounded HTTP lure proxy."""

from __future__ import annotations

import logging

import httpx
import pytest

from adam.sandbox.network.interceptor import NetworkInterceptor


@pytest.mark.asyncio
async def test_start_binds_real_port_and_stop_releases_it() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        assert interceptor.is_active is True
        assert interceptor.bound_port is not None
        assert interceptor.bound_port > 0
        assert interceptor.proxy_url.endswith(f":{interceptor.bound_port}")
    finally:
        port = interceptor.bound_port
        await interceptor.stop()

    assert interceptor.is_active is False
    assert interceptor.bound_port is None

    # The strongest cross-platform proof that the port was released: bind the
    # exact same port again immediately.
    rebound = NetworkInterceptor(port=port)
    await rebound.start()
    try:
        assert rebound.bound_port == port
    finally:
        await rebound.stop()


@pytest.mark.asyncio
async def test_registered_url_returns_fabricated_response() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        await interceptor.respond(
            "http://c2.test/beacon",
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            "X-C2-Id: 999\r\n\r\n{\"cmd\": \"sleep 60\"}",
        )

        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(
                f"http://127.0.0.1:{interceptor.bound_port}/beacon",
                headers={"Host": "c2.test"},
            )

        assert response.status_code == 200
        assert response.headers["x-c2-id"] == "999"
        assert response.json() == {"cmd": "sleep 60"}
    finally:
        await interceptor.stop()


@pytest.mark.asyncio
async def test_proxy_form_request_is_matched() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        await interceptor.respond("http://c2.test/beacon", "proxied-ok")
        async with httpx.AsyncClient(proxy=interceptor.proxy_url, timeout=2.0) as client:
            response = await client.get("http://c2.test/beacon")

        assert response.status_code == 200
        assert response.text == "proxied-ok"
    finally:
        await interceptor.stop()


@pytest.mark.asyncio
async def test_unmatched_url_returns_404() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(
                f"http://127.0.0.1:{interceptor.bound_port}/missing",
                headers={"Host": "c2.test"},
            )
        assert response.status_code == 404
        assert "no fabricated response" in response.text
    finally:
        await interceptor.stop()


@pytest.mark.asyncio
async def test_start_twice_raises() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            await interceptor.start()
    finally:
        await interceptor.stop()


@pytest.mark.asyncio
async def test_default_port_normalization() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        await interceptor.respond("http://c2.test:80/beacon", "default-port")
        assert interceptor.match_response("http://c2.test/beacon") is not None
        assert interceptor.match_response("http://C2.TEST:80/beacon") is not None
    finally:
        await interceptor.stop()


@pytest.mark.asyncio
async def test_path_only_fallback_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        await interceptor.respond("http://c2.test/beacon", "fallback")
        with caplog.at_level(logging.WARNING):
            rule = interceptor.match_response("/beacon")
        assert rule is not None
        assert any("Path-only fallback" in record.message for record in caplog.records)
    finally:
        await interceptor.stop()


@pytest.mark.asyncio
async def test_unrespond_removes_all_aliases() -> None:
    interceptor = NetworkInterceptor(port=0)
    await interceptor.start()
    try:
        await interceptor.respond("http://c2.test:80/beacon", "gone")
        await interceptor.unrespond("http://c2.test/beacon")
        assert interceptor.rules == {}
        assert interceptor.match_response("http://c2.test/beacon") is None
        assert interceptor.match_response("/beacon") is None
    finally:
        await interceptor.stop()
