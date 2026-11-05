# ADAM System: Known Limitations & Operational Boundaries

Operating boundaries as of November 6, 2026. Read the [documentation index](README.md), [historical live acceptance](repair-verification.md), and [latest frontend verification](frontend-verification.md) together: a passing historical run is not a current health assertion.

## Startup reliability and current health

**Open.** The November 5 acceptance recorded successful real control/treatment cycles and promotion of snapshot `4d3f4a15-5567-474f-96e5-208537a5ce4f`. During the November 6 frontend checks, VirtualBox state queries timed out before a new run could launch. The UI displays unavailable status and disables live launch. No new sample ran during that retest, and final powered-off/pristine health was not established then.

Guest readiness has a separate configurable deadline from the workload. A longer deadline alone does not establish readiness. The API's read-only status commands have five-second deadlines and clean up their own stalled query processes; this does not resolve general VirtualBox or boot reliability.

---

### 1. `FABRICATE_C2_RESPONSE` & `NETWORK`-Kind Mutations
- **Status**: **PARTIAL — bounded HTTP lure proxy (not transparent MITM)**
- **Root Cause**: The guest-resident HTTP agent (`adam_agent.ps1` / `HTTPGuestChannel`) executes mutations in-guest (filesystem, registry, processes) and cannot intercept or forge network packets from within the guest.
- **Resolution**:
  - `NetworkInterceptor` is a real host-side HTTP server that serves registered fabricated responses and returns `404`/`501` for everything else. It does not forward unmatched traffic.
  - `HTTPGuestChannel` configures the guest's WinINET/WinHTTP proxy once per session and optionally adds a marked hosts entry. Proxy configuration requires an administrator guest context; already-running clients may cache their old proxy.
  - `Runner` starts the interceptor before VM arming and stops it in `finally`, including on failure. Control sessions do not start it.
  - **Not covered**: HTTPS/`CONNECT` (returns `501`), raw sockets, non-HTTP protocols, pinned TLS, and clients that ignore the system proxy. SMB shares are registry-only; no real share or drive mapping is created.
  - See [`docs/network-interception.md`](./network-interception.md) for the exact transport and limitations.

---

### 2. `PLANT_DECOY_DOCUMENTS` Syntax / Timeout via `apply_mutation_batch`
- **Status**: **RESOLVED**
- **Root Cause**: `apply_mutation_batch` generated composite `cmd.exe /c` commands with embedded quotes. When formatted by `Common.psm1` (`Invoke-NativeProcess`), inner quotes were escaped as `\"`, causing `cmd.exe` to fail with `rc=1` or stall on `stdin`.
- **Resolution**: Rewrote `apply_mutation_batch` in `adam/sandbox/guest/http_channel.py`.
  - Manifests of files to create are serialized to JSON and Base64-encoded.
  - The script is executed directly via PowerShell `-EncodedCommand` with the Base64 payload embedded inside single quotes.
  - This bypasses `cmd.exe` entirely and eliminates all quoting, backslash, semicolon, and quote-stripping issues.
  - Added regression test suite in `tests/unit/test_apply_mutation_batch_quoting.py`.

---

### 3. Procmon & Tshark Telemetry in Golden Image
- **Status**: **Historically verified November 5; current VM health unverified**
- **Root Cause**: The legacy guest snapshot (`disk-resized-36gb-c-drive`) lacked `Procmon64.exe` and `tshark.exe` at standardized paths.
- **Resolution**:
  - The November 5 acceptance recorded one promoted snapshot named `clean`, UUID `4d3f4a15-5567-474f-96e5-208537a5ce4f`. The older recovery snapshot was deleted through guarded promotion. This dated inventory does not establish today's VM state.
  - Standardized the Procmon path on `C:\ADAM\Tools\Procmon\Procmon64.exe` across guest configuration, installer defaults, provisioning scripts, and host configurations.
  - The November 5 control/treatment cycles established readable Procmon and network exports and workload correlation. The November 6 frontend task did not repeat that live proof because state queries failed before launch.
  - Added automated guest provisioning script `scripts/provision_guest_tools.ps1` and verification harness `scripts/verify_candidate_snapshots.py`.
  - Added `tool_availability` caching to `HTTPGuestChannel` and updated `start_captures()` to gracefully skip missing tools when running with non-strict configuration.
  - Added `tools_required` configuration flag in `config/default.toml` and `adam/common/config.py` (default: `false` for graceful degradation; `true` for strict fail-fast mode).
  - The verification harness requires distinct candidate/recovery UUIDs, real pipeline artifacts and workload correlation, live Sysmon events, both restore cycles, and VM shutdown. Promotion is opt-in and blocked by incomplete evidence.

---

### 4. Synthetic Nature of `/simulate` API and Benchmark CLI
- **Status**: **PARTIAL — real engines wired; yield/plausibility still derived heuristics**
- **Root Cause**: `benchmark.py` originally hardcoded synthetic yield values (`ctrl_yield = 0.40`, `trt_yield = 0.85`), bypassing the real Fusion, Policy, and Deception engines.
- **Resolution**:
  - `adam/cli/benchmark.py` now wires `EventBus`, `EventFusionEngine`, `PolicyEngine`, and `DeceptionEngine` via `wire_engines()` for both Control (`dry_run=True`) and Treatment (`dry_run=False`) arms.
  - Event counts, decision counts, and primitive plausibility records are read from real pipeline executions.
  - **Still heuristic**: `yield_score` is a formula over mutation/semantic counts, and an empty-corpus plausibility fallback still exists. Neither is a measured exploitation metric or evidence of general detection effectiveness.
  - Added integration test `tests/integration/test_benchmark_real_engines.py` verifying real engine execution, input-sensitive metrics, and treatment yield advantage.

## Deployment and evidence availability

The React console is the primary interface; Streamlit remains a legacy option. Localhost development, stored sessions, and selected Chromium flows were checked. Production authentication, reverse-proxy deployment, other browsers, and a fresh real treatment run were not verified in the frontend task.

VM images, sample executables, local databases, raw captures, and verification logs are excluded from Git. Published screenshots show historical real evidence or explicitly synthetic results. They are not substitutes for reproducing live acceptance on a provisioned host.


