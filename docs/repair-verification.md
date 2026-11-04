# ADAM repair verification — 2026-11-04

**Overall status: VM acceptance is blocked. The software checks pass; a fully working VM baseline has not been established.**

## Implemented and checked

- Snapshot verification requires distinct UUIDs, real pipeline captures, workload correlation, live Sysmon delivery, state restoration, and clean shutdown. Promotion defaults to disabled, preserves recovery until verification succeeds, and checks orphan disk hashes.
- Execution deadlines fail explicitly and terminate only the owned sample PID. Lost process-start responses do not trigger duplicate launches. Exit codes are retained when available.
- Ingestion serializes live/exported writes, deduplicates after persistence, handles cancellation and retry, rolls back partial JSONL writes, and keeps Sysmon log generations separate.
- Missing polling responses raise failures. Historical HTTP Sysmon exports without a session watermark are retained as artifacts rather than ingested as current activity.
- Session records and reports contain source lifecycle outcomes, structured failures, execution PID/exit status, and cleanup failures. Final success is published after cleanup. Database shutdown retries are bounded.
- Guest scripts pass the Windows PowerShell 5.1 parser. The package includes a manifest and build identity. Provisioning uses host-staged, signature-verified installers and supports the standard Npcap graphical flow.
- Python 3.11 is the declared minimum; editable package discovery is explicit. Existing Ruff findings in `adam` and `frontend` were resolved.

Checks completed:

| Check | Result |
|---|---|
| Unit and integration suite, existing environment | 458 passed, 1 skipped (follow-up) |
| Same suite, previously installed fresh environment | 458 passed, 1 skipped (follow-up) |
| Fresh editable install and dependency check | Passed |
| Ruff, `adam` and `frontend` | Passed |
| CLI help, API/dashboard tests | Passed |
| Streamlit AppTest smoke | No application exceptions |
| Guest package manifest | Verified, build `8871352ee1e512a9` |
| Real verifier with identical candidate/recovery | Correctly refused, exit code 1 |

The tests emit a Starlette deprecation warning concerning its httpx test client. That warning did not fail either suite. Streamlit's smoke run also logged an unavailable backend request; it does not establish live VM connectivity.

## Real VM findings and remaining work

GuestControl executed real Windows commands and transferred the repair bundle. The guest independently reported a filtered, non-administrator token and no installed Tshark executable. The administrator installer prerequisite failed, and manually restarting the existing SYSTEM task returned access denied. HTTP readiness checks failed. The on-disk older agent manifest was `15dae6abf8b9c8a5`; this is not proof of the running build.

The desktop automation helper could not initialize, including after a reset: `failed to write kernel assets: The system cannot find the path specified`. The standard Npcap installer therefore could not be completed automatically.

The latest repair bundle is extracted at `C:\ADAM\repair`. Its transferred SHA-256 matched the host:

`ED3F25751AB8F1AB8CD29908420551D15AED1A63F1E31051F963161A0900794F`

One administrator action remains necessary: start the VM in its current state, run `C:\ADAM\repair\finish_guest_repair.ps1` in administrator PowerShell, and complete the Npcap wizard it opens. See [the provisioning guide](guest-provisioning-guide.md). Restoring the old recovery snapshot first would discard these staged files.

After that succeeds, the host agent must create a separate powered-off candidate, perform the two real capture cycles and controlled failure tests, and promote only if all acceptance checks pass. Procmon/Sysmon/Tshark workload correlation, candidate restoration, and promotion have **not** passed in this repair. No candidate was promoted or recovery snapshot deleted.

The preserved recovery snapshot is `clean`, UUID `b15f95bb-d463-444f-9019-c7dd966a7d90`, on VM `3c522809-b0f4-4d67-91a6-c587dc441b91`. It remains the older recovery baseline and must not be described as newly verified.

Final safety checks confirmed a normal Windows shutdown, VM state `poweroff`, exactly one unchanged `clean` recovery snapshot, and an unchanged SHA-256 for the unregistered VDI. The staged repair files remain in the current disk state.

## Evidence

### Follow-up execution attempt — 2026-11-04, 17:22 host time

Evidence directory: `logs/repair-verification/20261104-172228/`.

The VM was booted from its existing current state without restoring the recovery snapshot. GuestControl independently reconfirmed that the staged repair archive matches SHA-256 `ED3F25751AB8F1AB8CD29908420551D15AED1A63F1E31051F963161A0900794F`, the finishing script exists, Sysmon64 is running, and Tshark is absent. The available execution token remains non-administrator.

The desktop helper initialized, but listing applications failed with `Computer Use native pipe is unavailable: failed to connect native pipe: The system cannot find the file specified. (os error 2)`. Resetting and reconnecting produced the same error. The interactive Npcap wizard therefore remains blocked. No candidate creation or promotion was attempted.

The recovery snapshot UUID and the unrelated VDI SHA-256 (`ED73EB20445C0310C790129DF216DA88CB565F7BC05669573064FD2A646C3E1F`) matched the earlier inventory. Dependency consistency, Ruff, and the local guest manifest checks passed again. The full suite passed **458 tests with one skipped** in both the main and previously provisioned fresh environment. These checks do not establish that the guest deployment or capture pipeline works.

The HTTP agent briefly became reachable and identified its actual running build as `15dae6abf8b9c8a5`. A real HTTP execution confirmed SYSTEM privileges, so administrator access through that channel is possible when it is responsive. The first installation attempt exposed a Windows PowerShell 5.1 manifest bug: wrapping `ConvertFrom-Json` in `@(...)` nested the JSON array and rejected valid installers. Both provisioning scripts were corrected. Regression tests execute their actual integrity gates under PowerShell, accepting two valid entries and rejecting tampering.

The corrected installation request then lost its HTTP response. Its outcome is **unknown**, not a successful installation. Subsequent GuestControl inspection and script transfers timed out or failed to start a session. The stale session created by the timed-out probe was closed; a bounded retry also timed out. A normal ACPI shutdown request was accepted by VirtualBox but the guest remained running after 90 seconds. The VM was not forcibly powered off while installer state was unknown.

The corrected repair archive is available on the host at `logs/repair-verification/20261104-172228/guest-repair.zip`, SHA-256 `13820D9B6EB51C52AD27B84CC180E2DF9C160CF1DBDF713AD1D76EB397DEC731`. The original archive remains preserved in the earlier evidence directory. Corrected scripts could not be confirmed transferred into `C:\ADAM\repair`; do not rely on that directory's older scripts alone.

A temporary read-only VirtualBox share, `ADAMRepair`, exposes only the corrected archive and a hash-checking launcher. In administrator PowerShell inside the currently running guest, execute:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '\\VBOXSVR\ADAMRepair\complete_repair.ps1'
```

The launcher copies and extracts the corrected archive into the guest, then runs the finishing script. Resolve any already-running installer before retrying installation, and complete the Npcap wizard. The share is transient and disappears when the VM stops; if that happens before repair, the host must recreate it from the recorded handoff directory. Remove the share before candidate creation. The required powered-off final state, guest deployment gate, captures, failure scenarios, and snapshot promotion remain unverified.

Local evidence is retained under `logs/repair-verification/20261104-034410/`, including the before/after inventory, installer signatures, bundle transfer hashes, guest privilege/tool preflight, shutdown result, and orphan disk hashes. Suite logs are under `logs/repair-verification/`: `tests-final-verified.txt` and `tests-fresh-env.txt`. These local artifacts are excluded from Git.

The guest's Pacific local time differs from the host's India local time. That display difference alone is not a clock-drift finding.
