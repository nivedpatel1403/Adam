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
| Unit and integration suite, existing environment | 456 passed, 1 skipped |
| Same suite, freshly installed environment | 456 passed, 1 skipped |
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

Local evidence is retained under `logs/repair-verification/20261104-034410/`, including the before/after inventory, installer signatures, bundle transfer hashes, guest privilege/tool preflight, shutdown result, and orphan disk hashes. Suite logs are under `logs/repair-verification/`: `tests-final-verified.txt` and `tests-fresh-env.txt`. These local artifacts are excluded from Git.

The guest's Pacific local time differs from the host's India local time. That display difference alone is not a clock-drift finding.
