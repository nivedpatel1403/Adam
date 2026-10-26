"""
adam/sandbox/network/interceptor.py

Host-side, bounded HTTP lure proxy for ADAM's NETWORK mutations.

This is deliberately *not* a transparent MITM.  It is a real HTTP server that
serves fabricated responses for URLs registered by the deception engine.  The
guest reaches it through the system HTTP proxy (and, optionally, a hosts-file
redirect); clients that ignore the proxy or use pinned TLS are out of scope.
See docs/network-interception.md for the exact limitations.

The server binds to an ephemeral port by default so tests never depend on a
fixed port.  A live run can configure a host-only address and fixed port in
``[network]``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import web

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass
class FabricatedResponse:
    """One registered HTTP response, parsed from a primitive's raw value."""

    target_url: str
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    raw_response: str = ""


@dataclass
class InterceptedRequest:
    """A bounded, in-memory record of a request handled by the proxy."""

    method: str
    url: str
    matched: bool


def _normalize_url(url: str) -> str:
    """
    Return a canonical absolute URL with scheme/host lower-cased, a default
    path, and default ports removed.

    Accepts absolute URLs, scheme-relative URLs, host/path strings, and bare
    host:port endpoints emitted by the fusion bridge.
    """
    value = url.strip()
    if not value:
        return ""

    if value.startswith("//"):
        value = "http:" + value
    elif "://" not in value:
        # A bare path is not a URL; callers that need path-only matching use
        # _path_key() directly.
        if value.startswith("/"):
            return value
        value = "http://" + value

    parsed = urlsplit(value)
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        # Malformed/out-of-range port: leave the URL as-is so the caller can
        # decide it does not match anything.
        return value
    if port == _DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host
    if port is not None:
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _host_path_key(url: str) -> str:
    parsed = urlsplit(_normalize_url(url))
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port == _DEFAULT_PORTS.get(parsed.scheme):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    return f"{netloc}{parsed.path or '/'}"


def _path_key(url: str) -> str:
    parsed = urlsplit(_normalize_url(url))
    return parsed.path or "/"


class NetworkInterceptor:
    """
    Real host-side HTTP lure proxy.

    Lifecycle:
      - ``start()`` binds a socket and raises if already active or if the
        port cannot be bound;
      - ``stop()`` is idempotent and releases the port;
      - ``respond()`` / ``unrespond()`` register and remove rules while the
        server is running.

    ``rules`` remains the introspection dictionary keyed by the originally
    registered target URL.  Matching uses a normalized index so default
    ports, host casing, and proxy-form request URLs behave consistently.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        host_ip: str | None = None,
        max_request_log: int = 1000,
    ) -> None:
        self.host = host
        self.port = port
        self.host_ip = host_ip
        self.rules: dict[str, FabricatedResponse] = {}
        self.shares: dict[str, str | None] = {}
        self.request_log: list[InterceptedRequest] = []
        self._max_request_log = max(1, max_request_log)
        self._is_active = False
        self._bound_port: int | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._match_index: dict[str, FabricatedResponse] = {}
        self._path_index: dict[str, FabricatedResponse] = {}

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def bound_port(self) -> int | None:
        return self._bound_port

    @property
    def proxy_url(self) -> str:
        """The URL a guest proxy should use, e.g. http://192.168.56.1:8080."""
        port = self._bound_port if self._bound_port is not None else self.port
        public_host = self.host_ip or self.host
        if public_host in ("0.0.0.0", "::"):
            public_host = "127.0.0.1"
        return f"http://{public_host}:{port}"

    async def start(self) -> None:
        """Bind the proxy socket. Raises if already active or the port is busy."""
        if self._is_active:
            raise RuntimeError("NetworkInterceptor is already started")

        app = web.Application()
        app.router.add_route("*", "/", self._handle)
        app.router.add_route("*", "/{tail:.*}", self._handle)

        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            self._runner = None
            self._site = None
            raise

        self._runner = runner
        self._site = site
        self._bound_port = self._resolve_bound_port(runner)
        self._is_active = True
        logger.info(
            "[NetworkInterceptor] Started bounded HTTP lure proxy on %s:%d (proxy_url=%s)",
            self.host,
            self._bound_port,
            self.proxy_url,
        )

    @staticmethod
    def _resolve_bound_port(runner: web.AppRunner) -> int:
        addresses = getattr(runner, "addresses", None) or []
        if addresses:
            return int(addresses[0][1])
        # Fallback for aiohttp versions without AppRunner.addresses.
        for site in runner.sites:
            server = getattr(site, "_server", None)
            sockets = getattr(server, "sockets", None) if server else None
            if sockets:
                return int(sockets[0].getsockname()[1])
        raise RuntimeError("NetworkInterceptor started but could not determine its bound port")

    async def stop(self) -> None:
        """Stop the server, release the port, and clear registered state."""
        if not self._is_active and self._runner is None:
            return

        try:
            if self._site is not None:
                await self._site.stop()
        finally:
            if self._runner is not None:
                await self._runner.cleanup()
            self._site = None
            self._runner = None
            self._is_active = False
            self._bound_port = None
            self.clear_rules()
            self.shares.clear()
            logger.info("[NetworkInterceptor] Stopped bounded HTTP lure proxy")

    async def __aenter__(self) -> "NetworkInterceptor":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------ #
    # Rule registration / matching
    # ------------------------------------------------------------------ #

    def _parse_http_response(self, target_url: str, value: str | None) -> FabricatedResponse:
        """Parse a raw HTTP response or a bare body into a FabricatedResponse."""
        if not value:
            return FabricatedResponse(target_url=target_url, body=b"")

        if value.startswith("HTTP/"):
            parts = re.split(r"\r?\n\r?\n", value, maxsplit=1)
            header_part = parts[0]
            body_part = parts[1] if len(parts) > 1 else ""

            header_lines = header_part.splitlines()
            status_line = header_lines[0]
            status_match = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", status_line)
            if not status_match:
                raise ValueError(f"Malformed HTTP status line: {status_line!r}")
            status_code = int(status_match.group(1))

            headers: dict[str, str] = {}
            for line in header_lines[1:]:
                if ":" in line:
                    key, header_value = line.split(":", 1)
                    headers[key.strip().lower()] = header_value.strip()

            return FabricatedResponse(
                target_url=target_url,
                status_code=status_code,
                headers=headers,
                body=body_part.encode("utf-8"),
                raw_response=value,
            )

        return FabricatedResponse(
            target_url=target_url,
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            body=value.encode("utf-8"),
            raw_response=value,
        )

    async def respond(self, target_url: str, response_value: str | None) -> FabricatedResponse:
        """
        Register (or replace) a fabricated response for ``target_url``.

        The rule is indexed under the normalized full URL and its host+path
        form; path-only matching is kept as a last resort for requests whose
        Host header is unavailable.
        """
        if not target_url or not target_url.strip():
            raise ValueError("target_url must not be empty")
        normalized = _normalize_url(target_url)
        if normalized.startswith("/"):
            raise ValueError(
                f"NETWORK RESPOND requires an absolute URL or host/path, got {target_url!r}"
            )

        fab = self._parse_http_response(target_url, response_value)
        self.rules[target_url] = fab
        self._match_index[normalized] = fab
        self._match_index[_host_path_key(normalized)] = fab
        self._path_index[_path_key(normalized)] = fab
        logger.info(
            "[NetworkInterceptor] Registered C2 response rule for %s (status=%d, bytes=%d)",
            target_url,
            fab.status_code,
            len(fab.body),
        )
        return fab

    async def unrespond(self, target_url: str) -> None:
        """Remove a fabricated response rule and all of its matching aliases."""
        fab = self.rules.pop(target_url, None)
        if fab is None:
            # Allow callers to pass a normalized/aliased form.
            normalized = _normalize_url(target_url)
            fab = self._match_index.get(normalized) or self._match_index.get(
                _host_path_key(normalized)
            )
        if fab is not None:
            for key in [
                k for k, value in self._match_index.items() if value is fab
            ]:
                self._match_index.pop(key, None)
            for key in [
                k for k, value in self._path_index.items() if value is fab
            ]:
                self._path_index.pop(key, None)
            for key in [k for k, value in self.rules.items() if value is fab]:
                self.rules.pop(key, None)
            logger.info("[NetworkInterceptor] Removed C2 response rule for %s", target_url)

    def clear_rules(self) -> None:
        self.rules.clear()
        self._match_index.clear()
        self._path_index.clear()

    def match_response(self, request_url: str) -> FabricatedResponse | None:
        """Find a registered fabricated response for an incoming request URL."""
        if not request_url:
            return None

        if request_url.startswith("/") and not request_url.startswith("//"):
            return self._match_by_path(request_url)

        normalized = _normalize_url(request_url)
        rule = self._match_index.get(normalized)
        if rule is not None:
            return rule

        rule = self._match_index.get(_host_path_key(normalized))
        if rule is not None:
            return rule

        return self._match_by_path(_path_key(normalized))

    def _match_by_path(self, path: str) -> FabricatedResponse | None:
        rule = self._path_index.get(path)
        if rule is not None:
            logger.warning(
                "[NetworkInterceptor] Path-only fallback matched %s; "
                "register the full URL for reliable interception",
                path,
            )
        return rule

    # ------------------------------------------------------------------ #
    # HTTP handling
    # ------------------------------------------------------------------ #

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        if request.method.upper() == "CONNECT":
            self._record(request, request.raw_path, matched=False)
            return web.Response(
                status=501,
                text=(
                    "ADAM: HTTPS CONNECT is not intercepted by the bounded HTTP "
                    "lure proxy. Configure the client to use plain HTTP or add a "
                    "trusted CA and a real MITM proxy."
                ),
            )

        candidate = self._request_url(request)
        rule = self.match_response(candidate)
        self._record(request, candidate, matched=rule is not None)
        if rule is None:
            logger.info("[NetworkInterceptor] No fabricated response for %s", candidate)
            return web.Response(
                status=404,
                text=f"ADAM: no fabricated response for {candidate}",
            )

        headers = {
            key: value
            for key, value in rule.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding", "connection"}
        }
        logger.info(
            "[NetworkInterceptor] Served fabricated response for %s (status=%d, bytes=%d)",
            candidate,
            rule.status_code,
            len(rule.body),
        )
        return web.Response(status=rule.status_code, headers=headers, body=rule.body)

    @staticmethod
    def _request_url(request: web.Request) -> str:
        raw_path = request.raw_path
        if raw_path.startswith("http://") or raw_path.startswith("https://"):
            return raw_path

        host = request.host or request.headers.get("Host", "")
        if not host:
            return raw_path
        path = raw_path if raw_path.startswith("/") else "/" + raw_path
        return f"{request.scheme}://{host}{path}"

    def _record(self, request: web.Request, url: str, *, matched: bool) -> None:
        self.request_log.append(
            InterceptedRequest(method=request.method, url=url, matched=matched)
        )
        if len(self.request_log) > self._max_request_log:
            del self.request_log[: len(self.request_log) - self._max_request_log]

    # ------------------------------------------------------------------ #
    # SMB shares -- registry-only lure
    # ------------------------------------------------------------------ #

    async def mount_share(self, share_target: str, share_name: str | None) -> None:
        """
        Register a fake network share for introspection.

        This does NOT start an SMB server or create a Windows share.  The
        deception primitive writes the matching ``HKCU\\Network\\<Drive>``
        registry lure; docs/network-interception.md states the limitation.
        """
        self.shares[share_target] = share_name
        logger.info(
            "[NetworkInterceptor] Registered registry-only fake network share "
            "target=%s name=%s (no SMB transport)",
            share_target,
            share_name,
        )

    async def unmount_share(self, share_target: str) -> None:
        """Remove a registered fake share (registry lure revert)."""
        if share_target in self.shares:
            del self.shares[share_target]
            logger.info("[NetworkInterceptor] Unregistered fake network share target=%s", share_target)
