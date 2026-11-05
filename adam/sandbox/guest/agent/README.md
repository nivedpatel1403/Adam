# `adam/sandbox/guest/agent/`

This directory holds two conceptually different things that happen to share a name:

[Documentation index](../../../../docs/README.md) · [Guest setup](../../../../docs/guest-provisioning-guide.md) · [Verification status](../../../../docs/frontend-verification.md)

## `agent.py` — host-side, GuestControl-based (the "compatibility backend")

A Python class (`GuestAgent`) that runs **on the host**, driving VirtualBox's `VBoxManage
guestcontrol` to shell commands into the guest. Wrapped by `VBoxGuestChannel`
(`adam/sandbox/guest/vbox_channel.py`) to satisfy the `GuestChannel` interface
(`adam/sandbox/guest/channel.py`). Selected explicitly by `guest_backend = "vbox"`;
the committed configuration now selects `"http"`. Kept as a compatibility backend — see its own module docstring for its
extensive diagnostics/bug-fix history.

## `adam_agent.ps1` + `modules/*.psm1` — guest-resident (the target architecture)

A PowerShell 5.1 HTTP service that runs **inside the guest VM**, exposing the REST API
documented in `docs/phase5-http-agent-api.md`. Talked to from the host via `HTTPGuestChannel`
(`adam/sandbox/guest/http_channel.py`), selected by `guest_backend = "http"`.

This is not Python/FastAPI: ARCHITECTURE.md's
own constraint C4 ("The guest agent is PowerShell 5.1 compatible. No .NET Core assumption") rules
out installing a Python runtime into the guest image, so this implementation uses PowerShell
5.1's built-in `System.Net.HttpListener` instead.

**Deployment is separate from the host application.** Nothing under `modules/` or
`adam_agent.ps1` is imported by any Python code — copy this directory into the guest (or run
`install.ps1`, which does this for you) and register it as a Scheduled Task so it starts
automatically. See `install.ps1`'s own docstring for the one-time setup steps, and re-capture the
VM's `clean` snapshot afterward so the setup survives every session's snapshot restore.

**Verification history:** this README originally described an untested deployment. The
[November 5 acceptance](../../../../docs/repair-verification.md) subsequently recorded real
guest execution and capture. The [November 6 frontend retest](../../../../docs/frontend-verification.md)
was blocked before launch by VirtualBox state-query timeouts. Validate your own deployment;
historical acceptance does not establish current health.

| | `agent.py` | `adam_agent.ps1` / `modules/` |
|---|---|---|
| Runs on | Host | Guest |
| Language | Python | PowerShell 5.1 |
| Talks to guest via | VBoxManage guestcontrol (shell) | — (it *is* the guest process) |
| Talked to from host via | `VBoxGuestChannel` | `HTTPGuestChannel` |
| Backend selector | `guest_backend = "vbox"` | `guest_backend = "http"` |
