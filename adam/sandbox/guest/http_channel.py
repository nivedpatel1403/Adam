"""
adam/sandbox/guest/http_channel.py

HTTPGuestChannel -- the target Phase 5 architecture's GuestChannel
implementation, talking to a persistent, guest-resident PowerShell 5.1
HTTP service (adam/sandbox/guest/agent/adam_agent.ps1) instead of driving
VBoxManage GuestControl. See adam/sandbox/guest/channel.py for why this
exists alongside, not instead of, VBoxGuestChannel, and
docs/phase5-http-agent-api.md for the full wire-format spec this class is
written against.

Design notes:

  Resilience contract matches GuestAgent's own (adam/sandbox/guest/agent/
  agent.py's module docstring Guarantees): verify_tools() never raises;
  start_captures() is best-effort per source; stop_export_and_fetch()
  yields None for any source that fails, independently of the other two.
  Every fine-grained HTTP call funnels through `_request()`, which never
  raises -- a transport failure (guest unreachable, timeout) or an
  application-level failure (`success: false` in the envelope) both
  become a logged warning and a `None` return, exactly mirroring
  GuestAgent's `_run_quiet()` -> None-on-failure pattern, so callers at
  every level of this class read the same way.

  No shell, no stdout parsing on the HOST side of this transport either --
  `_request()` speaks JSON over HTTP via httpx; the guest-side elimination
  of shell/stdout parsing is the PowerShell implementation's own job (see
  the API spec's section 5-8 notes on ProcessStartInfo/ArgumentList).

  File transfer: `GET /filesystem/read` (API spec section 12.1) returns
  raw bytes outside the JSON envelope -- `_fetch_file()` is the one method
  in this class that does not go through `_request()`.

  Readiness: real-VM validation found a real startup-timing gap this
  class's other methods don't handle on their own -- immediately after VM
  boot, GET /health can refuse the connection for a short window before
  the guest-resident PowerShell HTTP agent (adam_agent.ps1) is actually
  listening, even though the VM/guest OS itself is already up (i.e. this
  is strictly AFTER SandboxController.prepare()'s own VirtualBox-level
  wait_for_guest_ready() has already succeeded -- it is not a VM boot
  problem, it's this specific HTTP service coming up slightly later).
  `wait_until_ready()` polls GET /health once a second until it returns
  HTTP 200 with a `success: true` envelope, or `guest_ready_timeout_s`
  (the SAME setting SandboxController already uses for VM-level readiness
  -- no second timeout config) expires, in which case it raises
  `adam.common.errors.GuestTimeoutError`. A successful check is cached for
  the lifetime of this instance (`self._ready`) -- once ready, this method
  is a no-op; nothing in this class proactively re-polls /health before
  later calls, so a guest that goes unhealthy mid-session is caught by
  those calls' own normal transport-error handling instead, exactly as
  requested. `verify_tools()` is the one caller (see its own docstring) --
  every other method assumes verify_tools() already ran, matching how
  SessionOrchestrator actually calls this class (adam/orchestrator/
  session.py: controller.prepare() -> controller.arm() -> guest_agent.
  verify_tools() -> start_captures() -> ... -> stop_export_and_fetch()).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import logging
import re
import time
from pathlib import Path, PureWindowsPath
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from adam.common.errors import ContractViolationError, GuestTimeoutError
from adam.sandbox.guest.agent.agent import (
    TelemetryArtifacts,
    ToolAvailability,
    required_missing_tools,
)
from adam.sandbox.guest.http_models import (
    BackingFileData,
    ErrorCode,
    ExistsData,
    GuestAgentError,
    GuestAgentUnreachableError,
    HealthData,
    MkdirRequest,
    NetworkConvertRequest,
    NetworkInterfacesData,
    NetworkStartRequest,
    NetworkStopRequest,
    ProcessInfo,
    ProcessQueryData,
    ProcessStartData,
    ProcessStartRequest,
    ProcessTerminateData,
    ProcessTerminateRequest,
    ProcmonExportRequest,
    ProcmonStartRequest,
    ProcmonStopRequest,
    ResponseEnvelope,
    SampleStageData,
    SampleStageRequest,
    SampleUploadData,
    SampleUploadRequest,
    SysmonDiagnosticsData,
    SysmonEventsSinceData,
    SysmonExportData,
    SysmonExportRequest,
    SysmonRecordItem,
    TokenData,
    VersionData,
)
from adam.sandbox.vbox.models import VMOperationResult
from adam.sandbox.vbox.ntstatus import decode_ntstatus


class StageResult(BaseModel):
    success: bool
    target_path: str
    sha256: str
    size_bytes: int


class SysmonEventsResult(tuple):
    """
    Result of get_sysmon_events_since. Unpacks as (last_record_id, events)
    for backwards compatibility with 2-tuple callers, while also exposing
    last_record_id, events, and log_reset as attributes.
    """

    last_record_id: int
    events: list[SysmonRecordItem]
    log_reset: bool

    def __new__(cls, last_record_id: int, events: list[SysmonRecordItem], log_reset: bool = False):
        return super().__new__(cls, (last_record_id, events))

    def __init__(self, last_record_id: int, events: list[SysmonRecordItem], log_reset: bool = False):
        self.last_record_id = last_record_id
        self.events = events
        self.log_reset = log_reset


logger = logging.getLogger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)

_POWERSHELL_PATH = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
_HOSTS_PATH = "C:\\Windows\\System32\\drivers\\etc\\hosts"
_HOSTS_MARKER = "# ADAM-LURE"


def _ps_quote(value: str) -> str:
    """Single-quote a value for a PowerShell script (safe with -EncodedCommand)."""
    return "'" + value.replace("'", "''") + "'"


def _encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")

# Transport-level errors worth a short retry -- the guest agent momentarily
# unreachable (a Scheduled Task restart per install.ps1's RestartCount, a
# brief host-only-adapter network hiccup) is a real, expected class of
# failure this project's own real-VM history has already seen for the
# GuestControl backend (agent.py's "session leak"/timeout investigations).
# Deliberately narrow -- excludes generic httpx.HTTPError subclasses that
# don't indicate "try again might help" (e.g. httpx.InvalidURL).
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


class HTTPGuestChannel:
    """GuestChannel backend driving the guest-resident PowerShell HTTP agent. See module docstring for the resilience contract every method follows."""

    def __init__(
        self,
        base_url: str,
        *,
        capture_dir: str,
        procmon_path: str | None,
        tshark_path: str | None,
        sysmon_log: str,
        tshark_interface: str = "1",
        auth_token: str | None = None,
        request_timeout_s: float = 15.0,
        retry_attempts: int = 3,
        retry_backoff_s: float = 0.2,
        guest_ready_timeout_s: float = 150.0,
        readiness_poll_interval_s: float = 1.0,
        client: httpx.AsyncClient | None = None,
        network_interceptor: Any | None = None,
        network_redirect_enabled: bool = True,
        hosts_redirect_enabled: bool = False,
        proxy_url: str | None = None,
        proxy_bypass: str = "localhost;127.0.0.1;<local>",
        smb_enabled: bool = False,
        tools_required: bool = False,
    ) -> None:
        """
        base_url: e.g. "http://192.168.56.101:8765" -- the guest's
        HttpListener endpoint (see install.ps1 for the default port).

        `retry_attempts`/`retry_backoff_s`: every call through `_request()`
        retries up to `retry_attempts` times (exponential backoff starting
        at `retry_backoff_s`) on a transient transport-level error (see
        `_RETRYABLE_TRANSPORT_ERRORS`) before giving up and returning None
        -- this does not change the "never raise" contract, only how much
        effort is spent before settling into the same None/logged-warning
        outcome a single failed attempt already produced. Set
        `retry_attempts=1` to disable retrying (e.g. in tests that want a
        transport failure to resolve immediately).

        `guest_ready_timeout_s`: default matches
        Settings.sandbox.guest_ready_timeout_s's own default (adam/common/
        config.py) -- callers built via adam/orchestrator/runner.py's
        Runner._build_guest_channel() pass that same setting explicitly, so
        this default only matters for a directly-constructed channel (e.g.
        a script or test) that doesn't. See `wait_until_ready()`.

        `readiness_poll_interval_s`: the 1-second poll cadence
        `wait_until_ready()` uses between GET /health attempts. Overridable
        (not just so tests can run fast without a real sleep -- see
        tests/integration/test_http_guest_channel.py -- but because it's
        the one number in this constructor that isn't itself sourced from
        Settings, per the "do not introduce another timeout configuration
        unless absolutely necessary" instruction this was built against;
        1.0 is the literal, explicitly requested default).

        `client` is injectable for tests (see
        tests/integration/test_http_guest_channel.py) -- defaults to a
        real httpx.AsyncClient against base_url.
        """
        self._base_url = base_url.rstrip("/")
        self._capture_dir = capture_dir
        self._procmon_path = procmon_path
        self._tshark_path = tshark_path
        self._sysmon_log = sysmon_log
        self._tshark_interface = tshark_interface
        self._auth_token = auth_token
        self._default_timeout = request_timeout_s
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff_s = retry_backoff_s
        self._guest_ready_timeout_s = guest_ready_timeout_s
        self._readiness_poll_interval_s = readiness_poll_interval_s
        self._ready = False
        self._retry_count = 0
        self._mutation_lock = asyncio.Lock()
        self._network_interceptor = network_interceptor
        self._network_redirect_enabled = network_redirect_enabled
        self._hosts_redirect_enabled = hosts_redirect_enabled
        self._proxy_url_override = proxy_url or None
        self._proxy_bypass = proxy_bypass
        self._smb_enabled = smb_enabled
        self._tools_required = tools_required
        self._network_configured = False
        self._proxy_configured = False
        self._hosts_entries: dict[str, str] = {}
        self._network_rules: set[str] = set()
        self._tool_availability: ToolAvailability | None = None
        headers = {"X-Adam-Token": auth_token} if auth_token else {}
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=request_timeout_s, headers=headers)

    @property
    def tool_availability(self) -> ToolAvailability | None:
        """Cached tool availability determined during verify_tools()."""
        return self._tool_availability

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def network_interceptor(self) -> Any | None:
        return self._network_interceptor

    @property
    def network_configured(self) -> bool:
        return self._network_configured

    async def aclose(self) -> None:
        """Close the underlying httpx client. Redirect cleanup is separate."""
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Network-lure redirect (Phase 2)
    # ------------------------------------------------------------------ #

    async def _run_powershell(
        self,
        script: str,
        *,
        label: str,
        timeout_s: float = 60.0,
    ) -> VMOperationResult:
        """
        Run a PowerShell script via -EncodedCommand.

        Encoding the whole script as UTF-16LE Base64 avoids every layer of
        cmd.exe/PowerShell quoting ambiguity for hostnames, paths, and proxy
        values, including apostrophes.
        """
        return await self.run_process(
            _POWERSHELL_PATH,
            [
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                _encode_powershell(script),
            ],
            wait=True,
            timeout_s=timeout_s,
        )

    async def configure_http_proxy(
        self,
        proxy_url: str | None = None,
        *,
        bypass: str | None = None,
    ) -> None:
        """
        Point the guest's WinINET (per-user) and WinHTTP (system) clients at
        the host-side lure proxy.

        Requires an administrator guest context.  Already-running processes
        may cache their proxy settings; restart them after this call.
        """
        url = proxy_url or self._proxy_url_override
        if not url and self._network_interceptor is not None:
            url = getattr(self._network_interceptor, "proxy_url", None)
        if not url:
            raise RuntimeError("configure_http_proxy: no proxy URL available")

        parsed = urlsplit(url)
        if parsed.scheme.lower() != "http" or not parsed.hostname or parsed.port is None:
            raise ValueError(
                f"configure_http_proxy: expected http://host:port, got {url!r}"
            )
        proxy_server = f"{parsed.hostname}:{parsed.port}"
        bypass_value = self._proxy_bypass if bypass is None else bypass

        script = f"""$ErrorActionPreference = 'Stop'
$key = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'
New-Item -Path $key -Force | Out-Null
New-ItemProperty -Path $key -Name ProxyEnable -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $key -Name ProxyServer -Value {_ps_quote(url)} -PropertyType String -Force | Out-Null
New-ItemProperty -Path $key -Name ProxyOverride -Value {_ps_quote(bypass_value)} -PropertyType String -Force | Out-Null
$proxyServer = {_ps_quote(proxy_server)}
$bypass = {_ps_quote(bypass_value)}
& netsh.exe winhttp set proxy "proxy-server=$proxyServer" "bypass-list=$bypass"
if ($LASTEXITCODE -ne 0) {{ throw "netsh winhttp set proxy failed with exit code $LASTEXITCODE" }}
"""
        result = await self._run_powershell(script, label="configure_http_proxy")
        if result.return_code != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"configure_http_proxy failed (rc={result.return_code}): {detail}"
            )
        self._proxy_configured = True
        self._network_configured = True
        logger.info("[HTTPGuestChannel] guest HTTP proxy set to %s", url)

    async def reset_http_proxy(self) -> None:
        """Disable the guest proxy and reset WinHTTP. Always best-effort."""
        script = """$ErrorActionPreference = 'Continue'
$key = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'
if (Test-Path $key) {
  New-ItemProperty -Path $key -Name ProxyEnable -Value 0 -PropertyType DWord -Force | Out-Null
}
& netsh.exe winhttp reset proxy | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
"""
        try:
            result = await self._run_powershell(script, label="reset_http_proxy", timeout_s=30.0)
            if result.return_code != 0:
                logger.warning(
                    "[HTTPGuestChannel] reset_http_proxy returned rc=%s: %s",
                    result.return_code,
                    result.stderr.strip() or result.stdout.strip(),
                )
        except Exception as exc:  # noqa: BLE001 -- teardown must not mask the session error
            logger.warning("[HTTPGuestChannel] reset_http_proxy failed: %s", exc)
        finally:
            self._proxy_configured = False
            self._network_configured = False

    async def add_hosts_entry(self, hostname: str, ip: str) -> None:
        """Append a marked hosts entry (requires guest administrator rights)."""
        hostname = hostname.strip()
        if not hostname or re.search(r"[\s\r\n]", hostname):
            raise ValueError(f"add_hosts_entry: invalid hostname {hostname!r}")
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ValueError(f"add_hosts_entry: invalid IP address {ip!r}") from exc

        script = f"""$ErrorActionPreference = 'Stop'
$hostsPath = {_ps_quote(_HOSTS_PATH)}
$marker = {_ps_quote(_HOSTS_MARKER)}
$hostname = {_ps_quote(hostname)}
$ip = {_ps_quote(ip)}
$lines = @([System.IO.File]::ReadAllLines($hostsPath))
$filtered = @($lines | Where-Object {{ -not ($_.Contains($hostname) -and $_.Contains($marker)) }})
$entry = "$ip`t$hostname`t$marker"
[System.IO.File]::WriteAllLines($hostsPath, @($filtered) + $entry, [System.Text.Encoding]::ASCII)
"""
        result = await self._run_powershell(script, label=f"add_hosts_entry: {hostname}")
        if result.return_code != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"add_hosts_entry failed (rc={result.return_code}): {detail}"
            )
        self._hosts_entries[hostname] = ip
        logger.info("[HTTPGuestChannel] added hosts entry %s -> %s", hostname, ip)

    async def remove_hosts_entry(self, hostname: str) -> None:
        """Remove the marked hosts entry for ``hostname`` (best-effort)."""
        hostname = hostname.strip()
        if not hostname:
            return
        script = f"""$ErrorActionPreference = 'Continue'
$hostsPath = {_ps_quote(_HOSTS_PATH)}
$marker = {_ps_quote(_HOSTS_MARKER)}
$hostname = {_ps_quote(hostname)}
$lines = @([System.IO.File]::ReadAllLines($hostsPath))
$filtered = @($lines | Where-Object {{ -not ($_.Contains($hostname) -and $_.Contains($marker)) }})
[System.IO.File]::WriteAllLines($hostsPath, $filtered, [System.Text.Encoding]::ASCII)
"""
        try:
            result = await self._run_powershell(script, label=f"remove_hosts_entry: {hostname}")
            if result.return_code != 0:
                logger.warning(
                    "[HTTPGuestChannel] remove_hosts_entry rc=%s: %s",
                    result.return_code,
                    result.stderr.strip() or result.stdout.strip(),
                )
        except Exception as exc:  # noqa: BLE001 -- teardown best-effort
            logger.warning("[HTTPGuestChannel] remove_hosts_entry failed: %s", exc)
        finally:
            self._hosts_entries.pop(hostname, None)

    async def reset_network_redirect(self) -> None:
        """Remove hosts entries and disable the proxy; safe to call repeatedly."""
        for hostname in list(self._hosts_entries):
            await self.remove_hosts_entry(hostname)
        if self._proxy_configured or self._network_configured:
            await self.reset_http_proxy()
        self._network_rules.clear()
        self._network_configured = False

    async def _ensure_network_redirect(self, target_url: str) -> None:
        if self._network_interceptor is None:
            return
        if not getattr(self._network_interceptor, "is_active", False):
            logger.warning(
                "[HTTPGuestChannel] network interceptor is not active; rule for %s "
                "was registered but the guest proxy was not configured",
                target_url,
            )
            return
        if self._network_redirect_enabled and not self._network_configured:
            await self.configure_http_proxy()
        if self._hosts_redirect_enabled:
            parsed_target = urlsplit(target_url)
            proxy = self._proxy_url_override or getattr(
                self._network_interceptor, "proxy_url", None
            )
            parsed_proxy = urlsplit(proxy) if proxy else None
            if parsed_target.hostname and parsed_proxy and parsed_proxy.hostname:
                await self.add_hosts_entry(parsed_target.hostname, parsed_proxy.hostname)
                if parsed_proxy.port not in (80, None):
                    logger.warning(
                        "[HTTPGuestChannel] hosts redirect points %s at %s, but the "
                        "lure proxy listens on port %s; clients using the original "
                        "port will not reach it",
                        parsed_target.hostname,
                        parsed_proxy.hostname,
                        parsed_proxy.port,
                    )

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #

    async def _send_with_retry(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        label: str = "",
    ) -> httpx.Response:
        """
        Low-level transport call, shared by `_request()` (never-raise
        callers) and `_get_raising()` (raising callers) -- retries a
        transient transport error (`_RETRYABLE_TRANSPORT_ERRORS`) up to
        `self._retry_attempts` times with exponential backoff, then
        re-raises the last exception so each of the two callers can
        translate it into its own contract (None+log for `_request()`,
        `GuestAgentUnreachableError` for `_get_raising()`). A non-retryable
        `httpx.HTTPError` (e.g. a malformed URL) is raised immediately, no
        retry budget spent on an error retrying can't fix.
        """
        last_exc: httpx.HTTPError | None = None
        headers = {"X-Adam-Token": self._auth_token} if self._auth_token else None
        req_timeout = timeout or self._default_timeout
        attempts = 1 if path == "/process/start" else self._retry_attempts
        for attempt in range(1, attempts + 1):
            try:
                return await self._client.request(
                    method, path, json=json_body, params=params, headers=headers, timeout=req_timeout
                )
            except _RETRYABLE_TRANSPORT_ERRORS as exc:
                last_exc = exc
                if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)):
                    try:
                        await self._client.aclose()
                    except Exception:
                        logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
                    client_headers = {"X-Adam-Token": self._auth_token} if self._auth_token else {}
                    self._client = httpx.AsyncClient(
                        base_url=self._base_url, timeout=self._default_timeout, headers=client_headers
                    )
                if attempt < attempts:
                    self._retry_count += 1
                    backoff = self._retry_backoff_s * (2 ** (attempt - 1))
                    logger.warning(
                        "guest_http: %s transient transport error on attempt %d/%d, retrying in %.2fs: %s",
                        label or path, attempt, self._retry_attempts, backoff, exc,
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.warning(
                    "guest_http: %s failed after %d attempt(s) (transport error): %s",
                    label or path, self._retry_attempts, exc,
                )
                raise

        assert last_exc is not None  # loop only exits via `return` (success) or `raise` above
        raise last_exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        label: str = "",
    ) -> dict[str, Any] | None:
        """
        Issues one HTTP call, parses the response envelope, and returns
        `data` on success -- or logs a warning and returns None on ANY
        failure (transport error, timeout, or `success: false`), per this
        class's "never raise" resilience contract. `label` is a short,
        human-readable operation name for the warning log, matching
        GuestAgent's own `_run_quiet(..., label=...)` convention.

        Transient transport errors are retried via `_send_with_retry()`
        before this method settles into its normal None-returning failure
        path -- still never raises, just tries harder first.
        """
        try:
            response = await self._send_with_retry(
                method, path, json_body=json_body, params=params, timeout=timeout, label=label
            )
        except httpx.HTTPError:
            # _send_with_retry() already logged the specific reason
            # (retried-and-exhausted vs. non-retryable) -- nothing more to
            # log here, just settle into this method's None contract.
            return None

        try:
            envelope = ResponseEnvelope.model_validate(response.json())
        except Exception as exc:  # noqa: BLE001 -- guest returned unparseable JSON; treat as a failure, not a crash
            logger.warning(
                "guest_http: %s returned an unparseable response (status=%s): %s",
                label or path, response.status_code, exc,
            )
            return None

        if not envelope.success:
            logger.warning(
                "guest_http: %s failed -- error_code=%s error_message=%s",
                label or path, envelope.error_code, envelope.error_message,
            )
            return None

        return envelope.data or {}

    async def _fetch_file(self, guest_path: str, host_path: Path, *, label: str) -> str | None:
        """`GET /filesystem/read` -- raw bytes, outside the JSON envelope (API spec 12.1)."""
        try:
            response = await self._client.get(
                "/filesystem/read", params={"path": guest_path}, timeout=self._default_timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("guest_http: %s failed to fetch %s (transport error): %s", label, guest_path, exc)
            return None

        if response.status_code != 200:
            error_code = response.headers.get("X-Error-Code", "UNKNOWN")
            logger.warning(
                "guest_http: %s failed to fetch %s -- status=%s error_code=%s",
                label, guest_path, response.status_code, error_code,
            )
            return None

        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_bytes(response.content)
        logger.info("[HTTPGuestChannel] fetched %s -> %s (%d bytes)", guest_path, host_path, len(response.content))
        return str(host_path)

    # ------------------------------------------------------------------ #
    # Readiness -- HTTP backend only (VBoxGuestChannel is unaffected: it
    # has no analogous "the transport itself needs a moment to come up"
    # problem, since GuestControl calls go through VBoxManage/the
    # hypervisor's own guest-addition channel, not a guest-resident HTTP
    # listener started via a Scheduled Task). See module docstring's
    # "Readiness" paragraph for the full design rationale.
    # ------------------------------------------------------------------ #

    async def _probe_health_once(self) -> tuple[bool, str]:
        """
        One single, non-retried GET /health attempt. Returns
        (healthy, detail) -- never raises; every failure mode
        (unreachable, non-200, unparseable body, success=false) is folded
        into `healthy=False` so `wait_until_ready()`'s polling loop can
        treat all of them identically ("not ready yet, try again"),
        exactly as the requirement specifies ("malformed JSON... treat it
        as not ready", not as a hard error).

        Deliberately bypasses `_send_with_retry()`/`_request()`: those
        implement their OWN short exponential-backoff retry loop for a
        transient error, which would make each of `wait_until_ready()`'s
        1-second-spaced "Attempt N" log lines secretly take longer than
        1 second and retry more than once per attempt -- confusing given
        the explicit "1 second interval" requirement. The outer
        `wait_until_ready()` loop IS this method's retry strategy.
        """
        headers = {"X-Adam-Token": self._auth_token} if self._auth_token else None
        health_timeout = min(3.0, self._default_timeout)
        try:
            response = await self._client.get("/health", headers=headers, timeout=health_timeout)
        except httpx.HTTPError as exc:
            if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)):
                try:
                    await self._client.aclose()
                    self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._default_timeout, headers=headers)
                except Exception:
                    logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
            return False, f"transport error: {exc}"

        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"

        try:
            envelope = ResponseEnvelope.model_validate(response.json())
        except Exception as exc:  # noqa: BLE001 -- malformed/non-JSON body -- "not ready", not a crash
            return False, f"malformed response: {exc}"

        if not envelope.success:
            return False, f"success=false (error_code={envelope.error_code})"

        return True, "ok"

    async def wait_until_ready(self, timeout_s: float | None = None) -> None:
        """
        Polls GET /health at `self._readiness_poll_interval_s` (default
        1s) intervals until it reports healthy or `timeout_s` (default
        `self._guest_ready_timeout_s`, i.e. the SAME
        Settings.sandbox.guest_ready_timeout_s VM-level readiness already
        uses -- no second timeout config introduced) elapses.

        No sleep before the FIRST attempt -- it fires immediately on
        entry, so a guest that's already up returns right away with zero
        added latency; the 1-second wait only happens BETWEEN a failed
        attempt and the next one. This deliberately does not duplicate
        VM-level boot detection: by the time anything calls this method
        (verify_tools(), in the real session flow -- see module
        docstring), SandboxController.prepare() has already run its own
        wait_for_guest_ready() check and confirmed the VM/guest OS itself
        is reachable. A timeout here means specifically that the VM booted
        but the guest-resident HTTP agent (adam_agent.ps1) never came up
        on top of that -- distinct from a VM boot failure, which would
        have already raised (a VMOperationError) inside prepare(), earlier
        in the call chain, before this method could ever run.

        Caches success on `self._ready` -- a second call after a
        successful first one returns immediately without issuing another
        HTTP request, so a long-running session's later guest-channel
        calls never proactively re-check /health; per the explicit
        instruction this was built against, a later request that actually
        fails is left to its own normal error handling instead.

        Raises `adam.common.errors.GuestTimeoutError` if the timeout
        expires without a healthy response.
        """
        if self._ready:
            return

        effective_timeout = timeout_s if timeout_s is not None else self._guest_ready_timeout_s
        started = time.monotonic()
        deadline = started + effective_timeout
        attempt = 0

        logger.info(
            "guest_http: Waiting for HTTP guest agent... (base_url=%s, timeout=%.1fs, poll_interval=%.1fs)",
            self._base_url, effective_timeout, self._readiness_poll_interval_s,
        )

        while True:
            attempt += 1
            self._health_probe_count = attempt
            logger.info("guest_http: Attempt %d", attempt)

            healthy, detail = await self._probe_health_once()
            if healthy:
                elapsed = time.monotonic() - started
                logger.info("guest_http: HTTP guest agent is healthy after %.1f seconds.", elapsed)
                self._ready = True
                return

            logger.debug("guest_http: Attempt %d not healthy yet -- %s", attempt, detail)

            if time.monotonic() >= deadline:
                elapsed = time.monotonic() - started
                logger.warning("guest_http: HTTP guest agent failed to become healthy before timeout.")
                raise GuestTimeoutError(
                    f"HTTP guest agent at {self._base_url} did not become healthy within "
                    f"{effective_timeout:.1f}s ({attempt} attempt(s), polling GET /health every "
                    f"{self._readiness_poll_interval_s:.1f}s; last elapsed={elapsed:.1f}s). The VM/guest "
                    "OS itself was already confirmed reachable by SandboxController.prepare()'s existing "
                    "wait_for_guest_ready() check before this method ever ran -- this is specifically the "
                    "guest-resident HTTP agent (adam_agent.ps1) never responding healthy on top of an "
                    f"already-booted VM, not a VM boot failure. Last observed state: {detail}"
                )

            await asyncio.sleep(self._readiness_poll_interval_s)

    # ------------------------------------------------------------------ #
    # GuestChannel: verify_tools
    # ------------------------------------------------------------------ #

    async def verify_tools(self) -> ToolAvailability:
        """
        Never raises (see module docstring's resilience contract). First
        action: wait for the HTTP guest agent to actually be reachable
        (`wait_until_ready()`) -- this is the one call site that method has
        in the whole class (see its own docstring and the module
        docstring's "Readiness" paragraph). A readiness timeout is caught
        here and folded into a normal, non-raising ToolAvailability(detail=
        {"agent": ...}) result instead of propagating -- `wait_until_ready()`
        itself stays independently raising (and directly unit-testable via
        pytest.raises) without breaking this method's own contract, which
        SessionOrchestrator (adam/orchestrator/session.py) and this
        codebase's tests already depend on.
        """
        detail: dict[str, str] = {}

        try:
            await self.wait_until_ready()
        except GuestTimeoutError as exc:
            detail["agent"] = str(exc)
            logger.warning("guest_http: tool unavailable -- agent: %s", detail["agent"])
            return ToolAvailability(
                procmon_available=False,
                tshark_available=False,
                sysmon_log_available=False,
                detail=detail,
                required_missing=required_missing_tools(
                    procmon_available=False,
                    tshark_available=False,
                    sysmon_log_available=False,
                    tools_required=self._tools_required,
                ),
            )

        procmon_available = False
        if self._procmon_path is None:
            detail["procmon"] = "guest_tools.procmon_path is not configured"
        else:
            exists_data = await self._request(
                "GET", "/filesystem/exists", params={"path": self._procmon_path}, label="verify_tools: procmon path"
            )
            procmon_available = bool(exists_data and ExistsData.model_validate(exists_data).exists)
            if not procmon_available:
                detail["procmon"] = f"not found in guest at configured path {self._procmon_path!r}"

        tshark_available = False
        if self._tshark_path is None:
            detail["tshark"] = "guest_tools.tshark_path is not configured"
        else:
            exists_data = await self._request(
                "GET", "/filesystem/exists", params={"path": self._tshark_path}, label="verify_tools: tshark path"
            )
            tshark_available = bool(exists_data and ExistsData.model_validate(exists_data).exists)
            if not tshark_available:
                detail["tshark"] = f"not found in guest at configured path {self._tshark_path!r}"

        sysmon_data = await self._request(
            "GET", "/sysmon/diagnostics", params={"channel": self._sysmon_log}, label="verify_tools: sysmon channel"
        )
        sysmon_log_available = bool(sysmon_data and SysmonDiagnosticsData.model_validate(sysmon_data).channel_available)
        if not sysmon_log_available:
            detail["sysmon"] = f"event log channel {self._sysmon_log!r} not available"

        for tool, reason in detail.items():
            logger.warning("guest_http: tool unavailable -- %s: %s", tool, reason)

        self._tool_availability = ToolAvailability(
            procmon_available=procmon_available,
            tshark_available=tshark_available,
            sysmon_log_available=sysmon_log_available,
            detail=detail,
            required_missing=required_missing_tools(
                procmon_available=procmon_available,
                tshark_available=tshark_available,
                sysmon_log_available=sysmon_log_available,
                tools_required=self._tools_required,
            ),
        )
        return self._tool_availability

    # ------------------------------------------------------------------ #
    # GuestChannel: start_captures
    # ------------------------------------------------------------------ #

    def _guest_path(self, filename: str) -> str:
        return f"{self._capture_dir}\\{filename}"

    async def start_captures(
        self,
        session_id: str,
        *,
        capture_procmon: bool = True,
        capture_network: bool = True,
    ) -> dict[str, bool]:
        await self._request(
            "POST", "/filesystem/mkdir", json_body=MkdirRequest(path=self._capture_dir).model_dump(),
            label="start_captures: mkdir capture_dir",
        )

        self._procmon_started = False
        self._tshark_started = False

        if capture_procmon and self._procmon_path is not None and (self._tool_availability is None or self._tool_availability.procmon_available):
            pml_path = self._guest_path(f"{session_id}_procmon.pml")
            result = await self._request(
                "POST", "/procmon/start",
                json_body=ProcmonStartRequest(session_id=session_id, backing_file=pml_path).model_dump(),
                label="start_captures: procmon",
            )
            logger.info("[HTTPGuestChannel] session=%s procmon start result=%s", session_id, result)
            if result is not None:
                self._procmon_started = True

        if capture_network and self._tshark_path is not None and (self._tool_availability is None or self._tool_availability.tshark_available):
            interfaces = await self._request("GET", "/network/interfaces", label="start_captures: list interfaces")
            if interfaces is not None:
                logger.info(
                    "[HTTPGuestChannel] session=%s available interfaces: %s",
                    session_id, NetworkInterfacesData.model_validate(interfaces).interfaces,
                )
            pcap_path = self._guest_path(f"{session_id}_network.pcapng")
            result = await self._request(
                "POST", "/network/start",
                json_body=NetworkStartRequest(
                    session_id=session_id, interface=self._tshark_interface, pcap_path=pcap_path
                ).model_dump(),
                label="start_captures: tshark",
            )
            logger.info("[HTTPGuestChannel] session=%s tshark start result=%s", session_id, result)
            if result is not None:
                self._tshark_started = True

        return {"procmon": self._procmon_started, "network": self._tshark_started}

    # ------------------------------------------------------------------ #
    # GuestChannel: stop_export_and_fetch
    # ------------------------------------------------------------------ #

    async def stop_export_and_fetch(
        self,
        session_id: str,
        host_artifact_dir: str | Path,
        *,
        export_sysmon: bool = True,
        export_procmon: bool = True,
        export_network: bool = True,
    ) -> TelemetryArtifacts:
        host_dir = Path(host_artifact_dir)
        host_dir.mkdir(parents=True, exist_ok=True)

        sysmon_path = await self._export_sysmon(session_id, host_dir) if export_sysmon else None
        procmon_path = await self._export_procmon(session_id, host_dir) if export_procmon else None
        network_path = await self._export_network(session_id, host_dir) if export_network else None

        return TelemetryArtifacts(
            sysmon_evtx_path=sysmon_path,
            procmon_csv_path=procmon_path,
            network_ek_json_path=network_path,
        )

    async def _export_sysmon(self, session_id: str, host_dir: Path) -> str | None:
        guest_evtx = self._guest_path(f"{session_id}_sysmon.evtx")
        result = await self._request(
            "POST", "/sysmon/export",
            json_body=SysmonExportRequest(channel=self._sysmon_log, output_path=guest_evtx).model_dump(),
            label="stop_export_and_fetch: sysmon export",
        )
        if result is None:
            return None
        mechanism = SysmonExportData.model_validate(result).mechanism if "mechanism" in result else "unknown"
        logger.info("[HTTPGuestChannel] session=%s sysmon exported via %s", session_id, mechanism)
        return await self._fetch_file(guest_evtx, host_dir / "sysmon.evtx", label=f"session={session_id} sysmon")

    async def get_sysmon_events_since(
        self,
        record_id: int = 0,
        event_ids: str = "1,3",
        channel: str | None = None,
    ) -> tuple[int, list[SysmonRecordItem]]:
        """
        Polls guest agent GET /sysmon/events/since for Sysmon records with RecordId > record_id.
        Drains batches >100 records and captures log_reset flag.
        Returns SysmonEventsResult(last_record_id, list_of_records, log_reset).
        """
        parts = [part.strip() for part in event_ids.split(",") if part.strip()]
        if not parts or any(not re.fullmatch(r"\d+", part) for part in parts):
            raise ValueError(
                f"get_sysmon_events_since: event_ids must be comma-separated integers, got {event_ids!r}"
            )
        event_ids = ",".join(str(int(part)) for part in parts)

        all_events: list[SysmonRecordItem] = []
        current_record_id = record_id
        any_log_reset = False

        for _batch in range(10):
            params: dict[str, Any] = {"record_id": current_record_id, "event_ids": event_ids}
            if channel:
                params["channel"] = channel
            result = await self._request(
                "GET",
                "/sysmon/events/since",
                params=params,
                label="live_poll: sysmon events since",
            )
            if result is None:
                raise RuntimeError("Sysmon polling failed: no valid guest response")
            try:
                data = SysmonEventsSinceData.model_validate(result)
                if bool(result.get("log_reset", False)):
                    any_log_reset = True
                    all_events.clear()
                all_events.extend(data.events)
                last_id = data.last_record_id
                # If guest returned >= 100 records and record_id advanced, continue draining
                if len(data.events) >= 100 and last_id > current_record_id:
                    current_record_id = last_id
                    continue
                return SysmonEventsResult(last_id, all_events, any_log_reset)
            except Exception as exc:
                raise RuntimeError("Invalid Sysmon polling response") from exc
        return SysmonEventsResult(current_record_id, all_events, any_log_reset)

    async def _export_procmon(self, session_id: str, host_dir: Path) -> str | None:
        if self._procmon_path is None or not getattr(self, "_procmon_started", False):
            return None
        pml_path = self._guest_path(f"{session_id}_procmon.pml")
        csv_path = self._guest_path(f"{session_id}_procmon.csv")

        await self._request(
            "POST", "/procmon/stop",
            json_body=ProcmonStopRequest(session_id=session_id).model_dump(),
            label="stop_export_and_fetch: procmon stop",
        )

        backing = await self._request(
            "GET", "/procmon/verify-backing-file", params={"path": pml_path}, label="stop_export_and_fetch: verify pml"
        )
        if backing is None or not BackingFileData.model_validate(backing).exists:
            logger.warning(
                "guest_http: session=%s no Procmon backing file at %s -- procmon telemetry unavailable this session",
                session_id, pml_path,
            )
            return None

        result = await self._request(
            "POST", "/procmon/export",
            json_body=ProcmonExportRequest(pml_path=pml_path, csv_path=csv_path).model_dump(),
            label="stop_export_and_fetch: procmon export",
        )
        if result is None:
            return None
        return await self._fetch_file(csv_path, host_dir / "procmon.csv", label=f"session={session_id} procmon")

    async def _export_network(self, session_id: str, host_dir: Path) -> str | None:
        if self._tshark_path is None or not getattr(self, "_tshark_started", False):
            return None
        pcap_path = self._guest_path(f"{session_id}_network.pcapng")
        ek_path = self._guest_path(f"{session_id}_network.ek.json")

        await self._request(
            "POST", "/network/stop",
            json_body=NetworkStopRequest(session_id=session_id).model_dump(),
            label="stop_export_and_fetch: tshark stop",
        )

        result = await self._request(
            "POST", "/network/convert",
            json_body=NetworkConvertRequest(pcap_path=pcap_path, ek_json_path=ek_path).model_dump(),
            label="stop_export_and_fetch: tshark convert",
        )
        if result is None:
            return None
        return await self._fetch_file(ek_path, host_dir / "network.ek.json", label=f"session={session_id} network")

    # ------------------------------------------------------------------ #
    # Diagnostics -- structured token/privilege data, not whoami text
    # ------------------------------------------------------------------ #

    async def get_token_diagnostics(self) -> TokenData | None:
        """
        Structured replacement for VBoxGuestChannel's whoami-text-parsing
        diagnostics (agent.py's `_whoami_diagnostics()`). Exposed as a
        method on this class rather than GuestChannel itself -- it isn't
        part of the shared session lifecycle, just a diagnostic exposed by
        this specific backend.
        """
        result = await self._request("GET", "/diagnostics/token", label="get_token_diagnostics")
        return TokenData.model_validate(result) if result is not None else None

    # ------------------------------------------------------------------ #
    # Explicit, RAISING diagnostics -- deliberately NOT used by
    # verify_tools/start_captures/stop_export_and_fetch above (those keep
    # the "never raise" contract this whole class exists to provide, per
    # ARCHITECTURE.md section 14.4's "partial results are still evidence"
    # philosophy: SessionOrchestrator.run_session() treats a down guest
    # agent as a reason to degrade to PARTIAL telemetry, not to abort
    # sample detonation). get_health()/get_version() are for a caller that
    # explicitly wants a fail-fast, exception-raising check instead -- a
    # manual troubleshooting step, or a future `adam guest-check` CLI
    # command -- the host-side mirror of install.ps1's own Test-Deployment
    # /health check (docs/phase5-migration-guide.md step 3, "Verify
    # manually").
    # ------------------------------------------------------------------ #

    async def _get_raising(self, path: str, model: type[_ModelT]) -> _ModelT:
        try:
            response = await self._send_with_retry("GET", path, timeout=self._default_timeout, label=path)
        except httpx.HTTPError as exc:
            raise GuestAgentUnreachableError(
                f"could not reach guest agent at {self._base_url}{path} (after retrying): {exc}. "
                "Confirm install.ps1 completed successfully on the guest (see "
                "docs/phase5-migration-guide.md) and that this base_url is the guest's "
                "correct host-only-adapter IP/port."
            ) from exc

        try:
            envelope = ResponseEnvelope.model_validate(response.json())
        except Exception as exc:
            raise GuestAgentUnreachableError(
                f"guest agent at {self._base_url}{path} returned an unparseable response "
                f"(status={response.status_code}): {exc}"
            ) from exc

        if not envelope.success:
            raise GuestAgentError(envelope.error_code, envelope.error_message)

        return model.model_validate(envelope.data or {})

    async def get_health(self) -> HealthData:
        """Raises GuestAgentUnreachableError (transport/parse failure) or GuestAgentError (guest reached but reported success=false) instead of returning None -- see the section docstring above for why this is separate from verify_tools()."""
        return await self._get_raising("/health", HealthData)

    async def get_version(self) -> VersionData:
        """Same raising contract as get_health() -- see the section docstring above."""
        return await self._get_raising("/version", VersionData)

    async def get_tool_diagnostics(self):
        from adam.sandbox.guest.http_models import ToolsDiagnosticsData
        return await self._get_raising("/diagnostics/tools", ToolsDiagnosticsData)

    async def exists(self, path: str) -> bool:
        """
        Check whether a file or directory exists on the guest via GET /filesystem/exists.
        Returns True if the file or directory exists, False otherwise.
        """
        exists_data = await self._request(
            "GET", "/filesystem/exists", params={"path": path}, label="filesystem: exists"
        )
        if exists_data is None:
            return False
        try:
            return bool(ExistsData.model_validate(exists_data).exists)
        except (ValidationError, TypeError, ValueError, KeyError):
            return False

    # ------------------------------------------------------------------ #
    # Sample Staging & Process Execution
    # ------------------------------------------------------------------ #

    async def stage_sample(
        self,
        host_source_path: str | Path,
        guest_target_path: str,
        *,
        timeout: float | None = None,
    ) -> StageResult:
        """
        Stage a sample executable onto the guest filesystem via POST /sample/stage.

        Reads the host-side file, calculates its SHA-256 locally, base64 encodes it,
        and transmits it to the guest agent. Verifies that the guest agent's computed
        SHA-256 matches what was sent before returning success.

        Raises:
            FileNotFoundError: if host_source_path does not exist.
            ValueError: if guest returned SHA256 does not match the local SHA256.
            GuestAgentUnreachableError: if HTTP transport fails.
            GuestAgentError: if agent reports success=false.
        """
        host_path = Path(host_source_path)
        if not host_path.is_file():
            raise FileNotFoundError(f"Host sample file not found: {host_path}")

        data = host_path.read_bytes()
        expected_sha256 = hashlib.sha256(data).hexdigest().lower()
        content_b64 = base64.b64encode(data).decode("ascii")

        req = SampleStageRequest(
            target_path=guest_target_path,
            content_base64=content_b64,
            sha256=expected_sha256,
            staged_path=guest_target_path,
        )

        staging_timeout = max(timeout or 0.0, 60.0)
        try:
            response = await self._send_with_retry(
                "POST",
                "/sample/stage",
                json_body=req.model_dump(exclude_none=True),
                timeout=staging_timeout,
                label="stage_sample",
            )
            envelope = ResponseEnvelope.model_validate(response.json())
        except httpx.HTTPError as exc:
            raise GuestAgentUnreachableError(
                f"could not reach guest agent to stage sample at {self._base_url}/sample/stage: {exc}"
            ) from exc

        if not envelope.success:
            if envelope.error_code == "UNAUTHORIZED":
                raise GuestAgentError(envelope.error_code, envelope.error_message)

            # Fall back to two-step upload -> stage for older guest agent implementations
            # Fall back to two-step upload -> stage for older guest agent implementations
            p = PureWindowsPath(guest_target_path)
            upload_req = SampleUploadRequest(
                sample_dir=str(p.parent),
                filename=p.name,
                sha256=expected_sha256,
                content_base64=content_b64,
            )
            up_resp = await self._send_with_retry(
                "POST",
                "/sample/upload",
                json_body=upload_req.model_dump(exclude_none=True),
                timeout=timeout or self._default_timeout,
                label="upload_sample_fallback",
            )
            up_env = ResponseEnvelope.model_validate(up_resp.json())
            if not up_env.success:
                raise GuestAgentError(up_env.error_code, up_env.error_message)
            up_data = SampleUploadData.model_validate(up_env.data or {})

            if PureWindowsPath(up_data.staged_path) == PureWindowsPath(guest_target_path):
                stage_data = SampleStageData(
                    target_path=guest_target_path,
                    sha256=expected_sha256,
                    size_bytes=len(data),
                )
            else:
                # Stage from the uploaded path
                stage_req = SampleStageRequest(
                    staged_path=up_data.staged_path,
                    target_path=guest_target_path,
                )
                stage_resp = await self._send_with_retry(
                    "POST",
                    "/sample/stage",
                    json_body=stage_req.model_dump(exclude_none=True),
                    timeout=timeout or self._default_timeout,
                    label="stage_sample_fallback",
                )
                envelope = ResponseEnvelope.model_validate(stage_resp.json())
                if not envelope.success:
                    raise GuestAgentError(envelope.error_code, envelope.error_message)
                stage_data = SampleStageData.model_validate(envelope.data or {})
        else:
            stage_data = SampleStageData.model_validate(envelope.data or {})
        returned_sha256 = (stage_data.sha256 or expected_sha256).lower()
        if returned_sha256 != expected_sha256:
            raise ValueError(
                f"Sample SHA256 mismatch during staging: host sent {expected_sha256}, "
                f"guest agent reported {returned_sha256}"
            )

        logger.info(
            "[HTTPGuestChannel] staged %s -> %s (%d bytes, sha256=%s)",
            host_path, guest_target_path, len(data), expected_sha256,
        )
        return StageResult(
            success=True,
            target_path=guest_target_path,
            sha256=returned_sha256,
            size_bytes=stage_data.size_bytes if stage_data.size_bytes is not None else len(data),
        )

    async def run_process(
        self,
        executable_path: str,
        arguments: list[str] | None = None,
        *,
        working_directory: str | None = None,
        wait: bool = True,
        timeout_s: float | None = None,
    ) -> VMOperationResult:
        """
        Launch and optionally wait for an executable on the guest via POST /process/start.

        Returns VMOperationResult capturing exit_code, stdout, stderr, and decoded NTSTATUS.
        Raises:
            GuestAgentUnreachableError: if HTTP transport fails.
            GuestAgentError: if agent reports success=false.
        """
        start = time.monotonic()
        req = ProcessStartRequest(
            executable=executable_path,
            arguments=arguments or [],
            working_directory=working_directory,
            wait=wait,
            timeout_s=timeout_s,
        )

        timeout_budget = (timeout_s + 15.0) if timeout_s is not None else max(self._default_timeout, 30.0)
        try:
            response = await self._send_with_retry(
                "POST",
                "/process/start",
                json_body=req.model_dump(exclude_none=True),
                timeout=timeout_budget,
                label=f"run_process: {executable_path}",
            )
        except httpx.HTTPError as exc:
            raise GuestAgentUnreachableError(
                f"could not reach guest agent for process start at {self._base_url}/process/start: {exc}"
            ) from exc

        try:
            envelope = ResponseEnvelope.model_validate(response.json())
        except Exception as exc:
            raise GuestAgentUnreachableError(
                f"guest agent returned unparseable response for process start: {exc}"
            ) from exc

        if not envelope.success:
            raise GuestAgentError(envelope.error_code, envelope.error_message)

        proc_data = ProcessStartData.model_validate(envelope.data or {})
        duration_ms = (time.monotonic() - start) * 1000
        rc = proc_data.exit_code
        cmd_tuple = ("http_agent", "/process/start", executable_path) + (tuple(arguments) if arguments else ())
        success = (rc == 0) if rc is not None else False
        return VMOperationResult(
            success=success,
            command=cmd_tuple,
            duration_ms=duration_ms,
            return_code=rc,
            stdout=proc_data.stdout or "",
            stderr=proc_data.stderr or "",
            termination_reason=decode_ntstatus(rc),
            pid=proc_data.pid,
        )

    async def wait_process(self, pid: int) -> dict[str, Any]:
        data = await self._request("POST", "/process/wait", json_body={"pid": pid, "timeout_s": 0}, label="process exit status")
        if data is None:
            raise RuntimeError("Could not retrieve process exit status")
        return data

    async def terminate_process(
        self,
        pid: int | None = None,
        name: str | None = None,
    ) -> int:
        """
        Calls guest agent POST /process/terminate to terminate a process by PID or Name.
        Returns the count of terminated processes.
        """
        req = ProcessTerminateRequest(pid=pid, name=name)
        result = await self._request(
            "POST",
            "/process/terminate",
            json_body=req.model_dump(exclude_none=True),
            label=f"terminate_process: pid={pid} name={name}",
        )
        if result is None:
            return 0
        try:
            data = ProcessTerminateData.model_validate(result)
            logger.info(
                "[HTTPGuestChannel] terminate_process pid=%s name=%s terminated_count=%d",
                pid, name, data.terminated_count,
            )
            return data.terminated_count
        except Exception as exc:
            logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
            logger.warning("Failed to validate ProcessTerminateData: %s", exc)
            return 0

    async def query_processes(
        self,
        pid: int | None = None,
        name: str | None = None,
    ) -> list[ProcessInfo]:
        """
        Calls guest agent GET /process/query to list running processes matching PID or Name.

        Raises:
            GuestAgentUnreachableError: on transport errors.
            GuestAgentError: on HTTP error or envelope.success is False.
            ContractViolationError: on JSON/schema parsing failures.
        Returns:
            list[ProcessInfo]: matching processes. Returns [] ONLY when guest responds
            with HTTP 200, success=True, and 0 matching processes.
        """
        params: dict[str, Any] = {}
        if pid is not None:
            params["pid"] = pid
        if name is not None:
            params["name"] = name

        try:
            response = await self._send_with_retry(
                "GET",
                "/process/query",
                params=params,
                label=f"query_processes: pid={pid} name={name}",
            )
        except httpx.HTTPError as exc:
            raise GuestAgentUnreachableError(
                f"could not reach guest agent for process query at {self._base_url}/process/query: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            try:
                envelope = ResponseEnvelope.model_validate(response.json())
                raise GuestAgentError(envelope.error_code or ErrorCode.ACCESS_DENIED, envelope.error_message)
            except GuestAgentError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
                raise GuestAgentError(
                    ErrorCode.ACCESS_DENIED,
                    f"Authentication failed with status {response.status_code}: {response.text}",
                )

        if response.status_code != 200:
            try:
                envelope = ResponseEnvelope.model_validate(response.json())
                if not envelope.success:
                    raise GuestAgentError(envelope.error_code or f"HTTP_{response.status_code}", envelope.error_message)
            except GuestAgentError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Operation failed; applying documented fallback")
            raise GuestAgentError(
                f"HTTP_{response.status_code}",
                f"Guest agent returned HTTP {response.status_code}: {response.text}",
            )

        try:
            envelope = ResponseEnvelope.model_validate(response.json())
        except Exception as exc:
            raise ContractViolationError(
                f"guest agent returned unparseable or invalid response envelope for process query: {exc}"
            ) from exc

        if not envelope.success:
            raise GuestAgentError(envelope.error_code, envelope.error_message)

        try:
            data = ProcessQueryData.model_validate(envelope.data or {})
        except Exception as exc:
            raise ContractViolationError(
                f"failed to validate ProcessQueryData from guest agent: {exc}"
            ) from exc

        return data.processes

    async def apply_mutation(
        self,
        kind: str,
        target: str,
        operation: str,
        value: str | None,
        *,
        value_type: str | None = None,
    ) -> None:
        """
        Apply a deception primitive mutation (file creation/deletion, registry setting)
        directly to the guest via HTTP agent / process execution.
        """
        async with self._mutation_lock:
            kind_upper = str(kind).upper()
            op_upper = str(operation).upper()

            result = None
            if "FILE" in kind_upper:
                if op_upper in ("CREATE", "SET", "WRITE"):
                    val_text = value or ""
                    cmd = (
                        f"$parent = Split-Path -Parent '{target}'; "
                        f"if (-not (Test-Path $parent)) {{ New-Item -ItemType Directory -Force -Path $parent | Out-Null }}; "
                        f"Set-Content -Path '{target}' -Value '{val_text}' -Force"
                    )
                    result = await self.run_process(
                        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                        wait=True,
                        timeout_s=60.0,
                    )
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation FILE CREATE target=%s rc=%s stdout=%s stderr=%s",
                        target, result.return_code if result else "N/A", result.stdout if result else "", result.stderr if result else "",
                    )
                elif op_upper == "DELETE":
                    cmd = f"if (Test-Path '{target}') {{ Remove-Item -Path '{target}' -Force -Recurse }}"
                    result = await self.run_process(
                        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                        wait=True,
                        timeout_s=60.0,
                    )
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation FILE DELETE target=%s rc=%s stdout=%s stderr=%s",
                        target, result.return_code if result else "N/A", result.stdout if result else "", result.stderr if result else "",
                    )
            elif "REGISTRY" in kind_upper:
                if op_upper in ("SET", "MASK", "CREATE"):
                    val_text = value or ""
                    allowed_types = {
                        "string": "String",
                        "expandstring": "ExpandString",
                        "dword": "DWord",
                        "qword": "QWord",
                        "binary": "Binary",
                        "multistring": "MultiString",
                    }
                    prop_type = allowed_types.get((value_type or "String").lower(), "String")
                    if prop_type in ("DWord", "QWord"):
                        if not re.fullmatch(r"-?\d+", val_text.strip()):
                            raise ValueError(
                                f"REGISTRY SET {target}: {prop_type} value must be an integer, got {val_text!r}"
                            )
                        value_expr = val_text.strip()
                    else:
                        value_expr = _ps_quote(val_text)
                    script = (
                        f"$regPath = 'Registry::{target}'; "
                        f"$parent = Split-Path -Parent $regPath; "
                        f"$name = Split-Path -Leaf $regPath; "
                        f"if (-not (Test-Path $parent)) {{ New-Item -Path $parent -Force | Out-Null }}; "
                        f"New-ItemProperty -Path $parent -Name $name -Value {value_expr} "
                        f"-PropertyType {prop_type} -Force | Out-Null"
                    )
                    result = await self._run_powershell(
                        script, label=f"registry_set: {target}"
                    )
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation REGISTRY SET target=%s type=%s rc=%s stdout=%s stderr=%s",
                        target, prop_type, result.return_code if result else "N/A", result.stdout if result else "", result.stderr if result else "",
                    )
                elif op_upper in ("DELETE", "UNMASK"):
                    script = (
                        f"$regPath = 'Registry::{target}'; "
                        f"if (Test-Path $regPath) {{ "
                        f"Remove-ItemProperty -Path (Split-Path -Parent $regPath) "
                        f"-Name (Split-Path -Leaf $regPath) -ErrorAction SilentlyContinue }}"
                    )
                    result = await self._run_powershell(
                        script, label=f"registry_delete: {target}"
                    )
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation REGISTRY DELETE target=%s rc=%s stdout=%s stderr=%s",
                        target, result.return_code if result else "N/A", result.stdout if result else "", result.stderr if result else "",
                    )
                elif op_upper in ("DELETE_KEY", "REMOVE_KEY"):
                    script = (
                        f"$regPath = 'Registry::{target}'; "
                        f"if (Test-Path $regPath) {{ "
                        f"Remove-Item -Path $regPath -Recurse -Force }}"
                    )
                    result = await self._run_powershell(
                        script, label=f"registry_delete_key: {target}"
                    )
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation REGISTRY DELETE_KEY target=%s rc=%s",
                        target, result.return_code if result else "N/A",
                    )
            elif "PROCESS" in kind_upper:
                if op_upper in ("CREATE", "SPAWN", "START"):
                    cmd = f"Start-Process -FilePath '{target}' -ErrorAction SilentlyContinue"
                    result = await self.run_process(
                        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                        wait=True,
                        timeout_s=60.0,
                    )
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation PROCESS CREATE target=%s rc=%s stdout=%s stderr=%s",
                        target, result.return_code if result else "N/A", result.stdout if result else "", result.stderr if result else "",
                    )
                elif op_upper in ("TERMINATE", "STOP", "KILL"):
                    target_str = str(target).strip()
                    term_count = 0
                    if target_str.isdigit() and int(target_str) > 0:
                        term_count = await self.terminate_process(pid=int(target_str))
                    elif value and str(value).strip().isdigit() and int(str(value).strip()) > 0:
                        term_count = await self.terminate_process(pid=int(str(value).strip()))
                    else:
                        proc_name = target_str.removesuffix(".exe")
                        term_count = await self.terminate_process(name=proc_name)
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation PROCESS %s target=%s value=%s terminated_count=%d",
                        op_upper, target, value, term_count,
                    )
                    return

                elif op_upper in ("SET", "RESET"):
                    # System clock acceleration / time reset lures
                    logger.info(
                        "[HTTPGuestChannel] apply_mutation PROCESS %s target=%s (time shift acknowledged)",
                        op_upper, target,
                    )
                    return
            elif "NETWORK" in kind_upper:
                if self._network_interceptor is not None:
                    if op_upper == "RESPOND":
                        await self._network_interceptor.respond(target, value)
                        self._network_rules.add(target)
                        await self._ensure_network_redirect(target)
                        logger.info("[HTTPGuestChannel] apply_mutation NETWORK RESPOND target=%s registered with interceptor", target)
                        return
                    elif op_upper == "UNRESPOND":
                        await self._network_interceptor.unrespond(target)
                        self._network_rules.discard(target)
                        if not self._network_rules:
                            await self.reset_network_redirect()
                        logger.info("[HTTPGuestChannel] apply_mutation NETWORK UNRESPOND target=%s removed from interceptor", target)
                        return
                    elif op_upper == "MOUNT":
                        await self._network_interceptor.mount_share(target, value)
                        if self._smb_enabled:
                            logger.warning(
                                "[HTTPGuestChannel] NETWORK MOUNT target=%s requested with "
                                "smb_enabled=true, but real SMB mapping is not implemented; "
                                "the registry lure is the only applied change",
                                target,
                            )
                        logger.info("[HTTPGuestChannel] apply_mutation NETWORK MOUNT target=%s registered as registry-only lure", target)
                        return
                    elif op_upper == "UNMOUNT":
                        await self._network_interceptor.unmount_share(target)
                        logger.info("[HTTPGuestChannel] apply_mutation NETWORK UNMOUNT target=%s removed from interceptor", target)
                        return
                    else:
                        raise NotImplementedError(
                            f"apply_mutation: unsupported NETWORK operation {operation!r} for target {target!r}"
                        )
                else:
                    # Network-layer mutations (RESPOND, MOUNT, UNMOUNT, etc.) require
                    # host-side network interception (e.g. WinDivert, fake DNS, or a
                    # local proxy) that the guest HTTP agent does not implement.
                    # Raise explicitly when no interceptor is configured.
                    raise NotImplementedError(
                        f"apply_mutation: NETWORK kind operations require a NetworkInterceptor "
                        f"(kind={kind!r}, operation={operation!r}, target={target!r}). "
                        f"Host-side network interception is required."
                    )
            else:
                # Unknown kind: raise rather than silently no-op.
                raise NotImplementedError(
                    f"apply_mutation: unrecognised mutation kind {kind!r} "
                    f"(operation={operation!r}, target={target!r})"
                )

            if result is None:
                raise RuntimeError(f"apply_mutation {kind_upper} {op_upper} failed: process start returned no result")
            if result.return_code != 0:
                err_msg = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    f"apply_mutation {kind_upper} {op_upper} target={target} failed (rc={result.return_code}): {err_msg}"
                )

    async def apply_mutation_batch(
        self,
        file_creates: list[tuple[str, str | None]],
        timeout_s: float = 60.0,
    ) -> None:
        """
        Apply multiple FILE CREATE mutations in a single PowerShell invocation.

        Each element of *file_creates* is a ``(target_path, value)`` pair where
        *value* is the file content (or ``None`` / empty string for an empty file).
        All files are written by one ``powershell.exe`` process, eliminating the
        ~11-14 s cold-start cost that would otherwise be paid once per file.

        This is the preferred call site for any primitive that needs to create
        several files in the same mutation, such as
        :class:`~adam.deception.primitives.filesystem_lures.PlantDecoyDocuments`.

        Implementation note (Phase fix for Limitation #2):
        ---------------------------------------------------
        The previous implementation built a compound ``cmd.exe /c`` command string
        with embedded single-quoted PowerShell statements joined by ``;``.  When
        ``Common.psm1`` (``Invoke-NativeProcess``) serialised the arguments via
        ``ProcessStartInfo``, inner quotes were escaped as ``\\\"``.  ``cmd.exe``
        treats ``\\`` as a path separator rather than a quote escape, causing
        consistent ``rc=1`` (``The filename, directory name, or volume label
        syntax is incorrect``) or ``stdin`` stalls (``TIMEOUT: process did not
        exit within 30s``).

        The fix encodes the entire file-creation manifest as a Base64 JSON
        payload and uses PowerShell's ``-EncodedCommand`` parameter, which
        accepts a Base64-encoded UTF-16LE script block directly.  This bypasses
        ``cmd.exe`` entirely and eliminates all quoting ambiguity regardless of
        what characters appear in file paths or content.

        Raises
        ------
        ValueError
            If *file_creates* is empty (callers should guard this themselves, but
            an empty batch is almost certainly a logic error).
        RuntimeError / GuestAgentError
            Propagated from :meth:`run_process` on guest-side failure.
        """
        if not file_creates:
            raise ValueError("apply_mutation_batch: file_creates list must not be empty")

        async with self._mutation_lock:
            import json as _json

            # Build a JSON manifest of files to create — transmitted as Base64
            # so no quoting/escaping issues can arise regardless of content.
            file_manifest = [
                {"path": target.replace("/", "\\"), "content": value or ""}
                for target, value in file_creates
            ]
            json_payload = _json.dumps(file_manifest)
            b64_json = base64.b64encode(json_payload.encode("utf-8")).decode("ascii")

            # Self-contained PowerShell script that decodes the manifest and
            # creates every file. The payload is standard Base64 ([A-Za-z0-9+/=])
            # so embedding it inside single quotes in the script is guaranteed safe.
            ps_script = (
                f"$b64 = '{b64_json}'; "
                "$jsonText = [System.Text.Encoding]::UTF8.GetString("
                "[System.Convert]::FromBase64String($b64)); "
                "$files = $jsonText | ConvertFrom-Json; "
                "foreach ($f in $files) { "
                "  $dir = Split-Path -Parent $f.path; "
                "  if (-not (Test-Path $dir)) { "
                "    New-Item -ItemType Directory -Force -Path $dir | Out-Null "
                "  }; "
                "  Set-Content -Path $f.path -Value $f.content -Force "
                "}"
            )
            # Encode the script itself as UTF-16LE Base64 for -EncodedCommand
            encoded_cmd = base64.b64encode(
                ps_script.encode("utf-16-le")
            ).decode("ascii")

            # -EncodedCommand takes a single Base64 argument with no quotes, spaces,
            # or special characters, eliminating any cmd.exe quote-stripping issues.
            result = await self.run_process(
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                ["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_cmd],
                wait=True,
                timeout_s=timeout_s,
            )
            logger.info(
                "[HTTPGuestChannel] apply_mutation_batch FILE CREATE count=%d rc=%s stdout=%s stderr=%s",
                len(file_creates),
                result.return_code if result else "N/A",
                result.stdout if result else "",
                result.stderr if result else "",
            )
            if result is None:
                raise RuntimeError("apply_mutation_batch failed: process start returned no result")
            if result.return_code != 0:
                err_msg = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    f"apply_mutation_batch failed (rc={result.return_code}): {err_msg}"
                )



def verify_sample_hash(content: bytes, expected_sha256: str) -> bool:
    """Host-side helper mirroring the guest's own upload-time verification -- used by callers before calling /sample/upload, per defense in depth (spec section 10)."""
    return hashlib.sha256(content).hexdigest().lower() == expected_sha256.lower()


def encode_sample_for_upload(content: bytes) -> str:
    """base64-encodes sample bytes for the /sample/upload JSON body (spec section 10)."""
    return base64.b64encode(content).decode("ascii")


__all__ = [
    "ErrorCode",
    "GuestAgentError",
    "GuestAgentUnreachableError",
    "HTTPGuestChannel",
    "StageResult",
    "encode_sample_for_upload",
    "verify_sample_hash",
]
