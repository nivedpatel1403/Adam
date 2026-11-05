# ADAM repair verification — 2026-11-05

> Historical acceptance record for November 5, 2026. The status, snapshot inventory, and shutdown below describe that handover. The [November 6 frontend check](frontend-verification.md) later encountered VirtualBox state-query timeouts and did not establish current VM health. React browser checks are documented separately there; the untested browser check below concerns the earlier dashboard task. Raw evidence paths are local references and are not published in Git.

**Recorded handover status: complete. Mandatory acceptance passed; exactly one verified `clean` snapshot remained, and the VM was powered off and restored to its pristine baseline.**

Guest installation is complete. No administrator provisioning action remains. This report supersedes the earlier installation/manual-action statements; the previous report is preserved in Git and in the local evidence directory.

## Deployment and snapshots

- VM: `Windows`, UUID `3c522809-b0f4-4d67-91a6-c587dc441b91`.
- Guest agent build: `a02fad5c6023a1fc`; manifest contains 13 deployed files. Candidate preparation verified their hashes and functional capture. Both final restore cycles automatically reached this build.
- Promoted snapshot: `clean`, UUID `4d3f4a15-5567-474f-96e5-208537a5ce4f`. Guarded promotion verified exactly one registered snapshot remains.
- Recovery `b15f95bb-d463-444f-9019-c7dd966a7d90` was retained through acceptance and then deleted through VirtualBox during guarded promotion. No VDI was manually deleted.
- Networking remains host-only, with every other VM network adapter disabled.
- Runtime changes were tested at revision `a200ee3c3cfb92801c87fa678de6d6a261644da8`. The final repository revision is recorded in the handover artifact.

Evidence: local evidence: candidate deployment and preparation — `logs/repair-verification/20261104-211334/candidate-defender.json`, local evidence: local manifest check — `logs/repair-verification/20261105-acceptance/manifest.txt`, local evidence: network isolation — `logs/repair-verification/20261105-acceptance/network-isolation.txt`, local evidence: hashed acceptance record — `logs/repair-verification/20261105-acceptance/acceptance.json`, local evidence: promotion and final health — `logs/repair-verification/20261105-acceptance/promotion.json`.

## Verified acceptance

| Requirement | Result and evidence |
|---|---|
| Control and treatment together | Both cycles passed, including automatic guest readiness, expected build, clean starting state, workload exit 0, cleanup, and normal shutdown. local evidence: Verifier result — `logs/repair-verification/20261105-acceptance/pipeline.json` |
| Sysmon | Unique workload events were persisted while the workload was alive, and correlated in exported evidence. local evidence: Cycle results and source outcomes — `logs/repair-verification/20261105-acceptance/pipeline.json` |
| Procmon | Readable exports correlated workload process, file, and registry activity. Control persisted 244,640 Procmon records; treatment persisted 362,671. local evidence: Durability audit — `logs/repair-verification/20261105-acceptance/durability-audit.json` |
| Network | Readable packet captures contained the unique controlled requests. Control persisted 474 packet records; treatment persisted 847. local evidence: Verifier result — `logs/repair-verification/20261105-acceptance/pipeline.json` |
| Fabricated response | Intended URL detected; response rule registered; fabrication mutation `APPLIED`; proxy logged serving it; guest response matched `fake_c2_payload`; proxy stopped and its port closed. local evidence: Proxy proof — `logs/repair-verification/20261105-acceptance/proxy-proof.json` |
| Restoration | A separately planted, read-back file and registry witness disappeared after candidate restoration; cycle-one markers were also absent. local evidence: Reset witness and second-cycle results — `logs/repair-verification/20261105-acceptance/pipeline.json` |
| Persistence, API, reports | Raw identities contain no duplicates, including live/export overlap. Durable semantic/decision/mutation counts, post-mutation counts, database metadata, API responses, and reports agree. local evidence: Final session audit — `logs/repair-verification/20261105-acceptance/durability-audit.json`, local evidence: failure-session audit — `logs/repair-verification/20261105-completion/failure-audit.json` |
| Controlled failures | Missing tool, unavailable polling, process timeout, and export failure all passed. Timeout stopped the owned workload and preserved the unrelated sentinel. Failed sessions retained partial artifacts, reported structured failures consistently, cleaned up, and powered off. local evidence: Four-case results — `logs/repair-verification/20261104-211334/failure-proxyexplicit/result.json` |
| Dashboard behavior | Streamlit AppTest passed against both final successful sessions and all four failed/partial sessions; source counts/errors, failure rendering, and deception-checkbox interaction were checked. local evidence: Dashboard results — `logs/repair-verification/20261105-acceptance/dashboard.json` |
| Repository | Both environments passed 535 tests with one skipped; Ruff, dependency consistency, manifest verification, installed CLI smoke checks, and 32 focused persistence/pipeline regressions passed. Fresh editable installation succeeded. local evidence: Repository checks — `logs/repair-verification/20261105-acceptance/repository-checks.json`, local evidence: fresh install — `logs/repair-verification/20261105-completion/fresh-install.txt` |

The final control session is `sess_2026_11_05_6d9d2c5b` (246,515 raw events and 10,528 semantic events/decisions). Treatment is `sess_2026_11_05_41e111ce` (366,616 raw events, 16,563 semantic events/decisions, four attempted mutations and three applied). A control policy proposal is not a mutation execution: deception is disabled for control, so its actual execution count is zero.

The acceptance record binds the tested revision, candidate UUID, guest build, checks, and SHA-256 hashes of the underlying session metadata, raw JSONL, Sysmon, Procmon, and packet artifacts. Promotion revalidated this record and checked free space and the unrelated VDI baseline.

## Improvements and slow-host findings

Both verification entry points now accept `--guest-ready-timeout`, defaulting to **900 seconds**, bounded to a maximum of 3,600 seconds. Readiness probes bound stalled HTTP requests. Boot readiness remains separate from workload execution deadlines (including the eight-second controlled timeout case). Reset-witness and final-baseline boots use the same selected deadline.

Automatic boot/build/tool readiness was demonstrated by the real restore cycles; increasing a timeout was not counted as proof. Host samples recorded available memory, paging, VM state, and agent build during verification. Available memory fell as low as 473 MB during this work, with substantial paging. Successful boots generally reached the agent in approximately two to three minutes. local evidence: Host observations — `logs/repair-verification/20261105-completion/boot-observations.jsonl`, local evidence: summary — `logs/repair-verification/20261105-acceptance/slow-host-summary.json`, local evidence: verifier log — `logs/repair-verification/20261105-acceptance/pipeline.log`.

The independent audit found real semantic-ID collisions in an earlier treatment: 17,697 emitted events produced only 17,695 database rows because two eight-character UUID prefixes already existed in older sessions. Semantic IDs now retain the full UUID. A deterministic matching-prefix regression covers the fix. Both real VM cycles were repeated afterward, and final durable counts agree. No old session was backfilled or rewritten to manufacture success. local evidence: Collision evidence — `logs/repair-verification/20261105-completion/semantic-id-collisions.json`, local evidence: failed earlier audit — `logs/repair-verification/20261105-completion/durability-audit.json`, local evidence: successful final audit — `logs/repair-verification/20261105-acceptance/durability-audit.json`.

Earlier failed readiness, crash, proxy-bypass, and ingestion attempts remain preserved under `logs/repair-verification/20261104-211334/` and `logs/repair-verification/20261105-completion/`. Those attempts are historical failures, not final acceptance evidence. Guest Defender maintenance and its diagnostic evidence remain in the earlier directory; these finite successful runs do not establish the cause of every historical boot failure.

## Not tested and limitations

Browser visual inspection remains **not tested**: Computer Use exposed no browsers, and its native pipe returned OS error 2. This is separate from the passing AppTest behavior checks. local evidence: Browser availability record — `logs/repair-verification/20261105-completion/browser-check.json`.

The interception proof covers a controlled plain-HTTP client that explicitly reads and uses ADAM's configured proxy. It does not demonstrate transparent interception of clients that ignore proxy settings, HTTPS CONNECT interception, or TLS-pinned traffic. No internet access was required for the workload.

The suites emit one non-failing Starlette/httpx deprecation warning and include one skipped test. No disk-exhaustion fault was induced.

## Recorded final handover

The final `clean` boot automatically reached the expected agent build. All 13 deployed file hashes matched; Procmon, Tshark, Sysmon and Npcap were healthy. The guest proxy was disabled, WinHTTP reported direct access, no capture processes remained, and restoration witnesses were absent. Windows requested normal poweroff, followed by a successful final restore. Exactly one `clean` remains and VM state is `poweroff`. The unrelated VDI SHA-256 remains `ed73eb20445c0310c790129df216da88cb565f7bc05669573064fd2a646c3e1f`.

VirtualBox briefly rejected a `showvminfo` query as its console closed during the final normal shutdown. The initial wrapper result is preserved as failed; no additional consolidation was attempted. The saved VM log confirms a guest-requested poweroff. A subsequent stable query, final restore, snapshot inventory, and independent VDI hash check all passed. local evidence: Saved shutdown log — `logs/repair-verification/20261105-acceptance/final-shutdown-vbox.log`, local evidence: completed finalization — `logs/repair-verification/20261105-acceptance/finalization.json`.

No guest setup or snapshot work remains. Repair-owned host services and the temporary keep-awake helper are released at handover; the machine-readable record contains the final cleanup and repository revision. Browser visual inspection is the sole untested optional check.

local evidence: Final health and promotion result — `logs/repair-verification/20261105-acceptance/promotion.json`, local evidence: handover record — `logs/repair-verification/20261105-acceptance/handover.json`.

Final documentation revision checks: local evidence: main suite — `logs/repair-verification/20261105-acceptance/suite-handover-main.txt`, local evidence: fresh suite — `logs/repair-verification/20261105-acceptance/suite-handover-fresh.txt`.
