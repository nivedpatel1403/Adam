# ADAM System: Known Limitations & Operational Boundaries

This document lists the known architectural and operational limitations of the ADAM automated malware analysis and dynamic deception system, along with their resolution status following the implementation plan.

---

### 1. `FABRICATE_C2_RESPONSE` & `NETWORK`-Kind Mutations
- **Status**: **RESOLVED**
- **Root Cause**: The guest-resident HTTP agent (`adam_agent.ps1` / `HTTPGuestChannel`) executes mutations in-guest (filesystem, registry, processes) and cannot intercept or forge network packets from within the guest.
- **Resolution**: Implemented host-side network interception via `adam.sandbox.network.NetworkInterceptor`.
  - Added `NetworkInterceptor` (`adam/sandbox/network/interceptor.py`) to manage C2 response fabrication and SMB share redirection on the host.
  - Updated `HTTPGuestChannel` to accept an optional `network_interceptor` and delegate `NETWORK` kind mutations (`RESPOND`, `UNRESPOND`, `MOUNT`, `UNMOUNT`) to it.
  - Updated `adam/orchestrator/runner.py` to construct and inject `NetworkInterceptor` when running HTTP guest backend.
  - Added unit test suite in `tests/unit/test_network_interceptor.py`.

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
- **Status**: **RESOLVED (Tooling + Graceful Degradation)**
- **Root Cause**: The golden guest snapshot (`disk-resized-36gb-c-drive`) lacked `Procmon64.exe` and `tshark.exe` at configured paths.
- **Resolution**:
  - Created automated guest provisioning script `scripts/provision_guest_tools.ps1` to download and install Process Monitor, Wireshark/Tshark, accept EULAs, and check Sysmon.
  - Authored comprehensive guide in `docs/guest-provisioning-guide.md` with snapshot instructions.
  - Added `tool_availability` caching to `HTTPGuestChannel` and updated `start_captures()` to gracefully skip missing tools rather than producing error cascades.
  - Added `tools_required` configuration flag in `config/default.toml` and `adam/common/config.py` (default: `false` for graceful degradation; `true` for strict fail-fast mode).

---

### 4. Synthetic Nature of `/simulate` API and Benchmark CLI
- **Status**: **RESOLVED (Real-Engine Hybrid Replay)**
- **Root Cause**: `benchmark.py` hardcoded synthetic yield values (`ctrl_yield = 0.40`, `trt_yield = 0.85`), bypassing the real Fusion, Policy, and Deception engines.
- **Resolution**:
  - Rewrote `adam/cli/benchmark.py` to wire `EventBus`, `EventFusionEngine`, `PolicyEngine`, and `DeceptionEngine` via `wire_engines()` for both Control (`dry_run=True`) and Treatment (`dry_run=False`) arms.
  - Benchmark now derives empirical paired yields, event counts, decision counts, and primitive plausibility metrics directly from pipeline executions.
  - Enhanced `/simulate` endpoint in `adam/api/main.py` with proper database commits and bus drain synchronization.
  - Added integration test `tests/integration/test_benchmark_real_engines.py` verifying real engine execution, input-sensitive metrics, and treatment yield advantage.


