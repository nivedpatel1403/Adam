"""
adam/sandbox/network/interceptor.py

Host-side network interception and mutation handling for ADAM.
Enables fabrication of C2 beacon responses and fake network share mounting.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class FabricatedResponse:
    target_url: str
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    raw_response: str = ""


class NetworkInterceptor:
    """
    Host-side interceptor that intercepts and manipulates network traffic
    for guest VMs on host-only or NAT networks.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        enable_mitmproxy: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.enable_mitmproxy = enable_mitmproxy
        self.rules: dict[str, FabricatedResponse] = {}
        self.shares: dict[str, str | None] = {}
        self._is_active: bool = False

    @property
    def is_active(self) -> bool:
        return self._is_active

    async def start(self) -> None:
        """Starts host-side network interception."""
        self._is_active = True
        logger.info("[NetworkInterceptor] Started host-side interception on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stops interception and cleans up active shares/rules."""
        for target in list(self.shares.keys()):
            await self.unmount_share(target)
        self.rules.clear()
        self._is_active = False
        logger.info("[NetworkInterceptor] Stopped host-side interception")

    async def __aenter__(self) -> NetworkInterceptor:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    def _parse_http_response(self, target_url: str, value: str | None) -> FabricatedResponse:
        """Parses raw HTTP response string or body value into FabricatedResponse."""
        if not value:
            return FabricatedResponse(target_url=target_url, body=b"")

        # Check if value is a full HTTP response (e.g. "HTTP/1.1 200 OK\r\n...")
        if value.startswith("HTTP/"):
            parts = value.split("\r\n\r\n", 1)
            if len(parts) == 1:
                parts = value.split("\n\n", 1)
            header_part = parts[0]
            body_part = parts[1] if len(parts) > 1 else ""

            header_lines = header_part.splitlines()
            status_line = header_lines[0]
            status_match = re.search(r"HTTP/\d\.\d\s+(\d+)", status_line)
            status_code = int(status_match.group(1)) if status_match else 200

            headers: dict[str, str] = {}
            for line in header_lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

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

    async def respond(self, target_url: str, response_value: str | None) -> None:
        """
        Registers a fabricated C2 response rule for target_url.
        When traffic matches target_url, this response is served.
        """
        fab = self._parse_http_response(target_url, response_value)
        self.rules[target_url] = fab
        logger.info(
            "[NetworkInterceptor] Registered C2 response rule for %s (status=%d, bytes=%d)",
            target_url,
            fab.status_code,
            len(fab.body),
        )

    async def unrespond(self, target_url: str) -> None:
        """Removes the fabricated response rule for target_url (revert operation)."""
        if target_url in self.rules:
            del self.rules[target_url]
            logger.info("[NetworkInterceptor] Removed C2 response rule for %s", target_url)

    async def mount_share(self, share_target: str, share_name: str | None) -> None:
        """
        Creates or registers a fake network share.
        """
        self.shares[share_target] = share_name
        logger.info(
            "[NetworkInterceptor] Mounted fake network share target=%s name=%s",
            share_target,
            share_name,
        )

    async def unmount_share(self, share_target: str) -> None:
        """
        Unmounts/removes a fake network share (revert operation).
        """
        if share_target in self.shares:
            del self.shares[share_target]
            logger.info("[NetworkInterceptor] Unmounted fake network share target=%s", share_target)

    def match_response(self, request_url: str) -> FabricatedResponse | None:
        """Finds any registered fabricated response matching request_url."""
        if request_url in self.rules:
            return self.rules[request_url]
        parsed_req = urlparse(request_url)
        for target, rule in self.rules.items():
            parsed_rule = urlparse(target)
            if (
                parsed_rule.netloc.lower() == parsed_req.netloc.lower()
                and parsed_rule.path.rstrip("/") == parsed_req.path.rstrip("/")
            ):
                return rule
        return None
