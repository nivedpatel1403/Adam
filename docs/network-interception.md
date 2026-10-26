# Network interception: what ADAM actually does

ADAM uses a **bounded HTTP lure proxy**, not a transparent MITM. This page is
the operational contract for `FABRICATE_C2_RESPONSE` and
`MOUNT_FAKE_NETWORK_SHARE`.

## What is intercepted

| Client behavior | Intercepted? | How |
|---|---|---|
| HTTP request sent through the guest's WinINET/WinHTTP proxy | **Yes** | `NetworkInterceptor` serves the registered fabricated response. |
| HTTP request whose hostname is redirected by the hosts file to the proxy IP **and** uses the proxy's port | **Yes** | The request reaches the proxy directly. |
| HTTPS / `CONNECT` | **No** | The proxy returns `501`; it does not terminate TLS or present a CA. |
| Raw sockets, custom TCP, non-HTTP protocols | **No** | The proxy is an HTTP server only. |
| TLS with certificate pinning | **No** | Same as HTTPS. |
| SMB share access | **No** | The share lure is registry-only; no SMB server or Windows share is created. |

## Lifecycle

1. `Runner` constructs one `NetworkInterceptor` for the HTTP guest backend and
   starts it before `SessionOrchestrator.run_session()` (before the VM is armed
   or the sample is staged). The interceptor binds an ephemeral port by default;
   the actual port is available as `bound_port` / `proxy_url`.
2. On the first `NETWORK RESPOND` mutation, `HTTPGuestChannel` configures the
   guest's WinINET (per-user) and WinHTTP (system) proxy to `proxy_url`, once per
   session. This requires an administrator guest context.
3. If `hosts_redirect_enabled=true`, the channel also appends a marked
   `# ADAM-LURE` entry to the guest hosts file. A hosts entry only reaches the
   proxy when the requested port matches the proxy's port; otherwise the
   original port is used and the request will not hit the proxy.
4. On session teardown, the channel removes its marked hosts entries and
   disables the guest proxy, then `Runner` stops the interceptor and releases
   the port. Cleanup is best-effort and always attempted.

Control sessions do **not** start the interceptor or configure the guest proxy.
The control arm therefore observes real guest/network behavior without
fabricated responses.

## Configuration

```toml
[http_guest]
network_redirect_enabled = true
hosts_redirect_enabled = false
proxy_url = ""              # empty: derive from [network].host_ip + bound port
proxy_bypass = "localhost;127.0.0.1;<local>"

[network]
host = "0.0.0.0"            # bind address on the host
port = 0                    # 0 = OS-assigned ephemeral port
host_ip = "192.168.56.1"    # address the GUEST uses to reach the host
smb_enabled = false         # registry-only lure; no SMB transport
```

For a live host-only VM, set `host_ip` to the host-only adapter address. The
guest must be able to reach it. If the adapter address differs, override it in
`config/<ADAM_ENV>.toml`.

## Matching rules

Registered target URLs are normalized (scheme/host lower-cased, default ports
removed, path defaulted to `/`). Matching tries, in order:

1. the normalized full URL;
2. host + path (so a rule without a query matches a request with one);
3. path only, with a warning (last resort for requests without a usable Host).

Unknown URLs receive `404` with `ADAM: no fabricated response ...`. `CONNECT`
receives `501`. The proxy never forwards unmatched traffic upstream.

## Registry-only network share

`MOUNT_FAKE_NETWORK_SHARE` writes the five values Windows uses under
`HKCU\Network\<Drive>` (`RemotePath`, `ProviderName`, `ConnectionType`,
`UserName`, `DeferFlags`) and records a `NETWORK MOUNT` intent. It does **not**
create a real share or map a drive. This is intentional: a real share requires
host administrator rights, firewall changes, credentials, and a share lifecycle.
`smb_enabled=true` currently logs a warning and still applies only the registry
lure.

## Failure behavior

- Interceptor bind failure aborts the run before VM arming.
- Guest proxy configuration failure fails the mutation; it is not silently
  reported as applied.
- Cleanup failures are logged and do not mask the session result.
- The interceptor is stopped in `Runner`'s `finally` block, including on session
  failure, so a failed run does not leave a port bound.
