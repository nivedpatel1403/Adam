# Console launch repair — November 6, 2026

This follow-up investigates the disabled Launch control after the frontend was started. It supersedes the earlier status-query blocker only to the extent verified below; historical failed sessions remain unchanged.

## Findings and changes

- The initial failure was a stalled VirtualBox management process. Its log recorded a pending lock request for `Windows`; read-only queries timed out despite no running VM, active harness, or VirtualBox Manager. Diagnostics were saved before restarting the idle, user-owned `VBoxSVC` process. A subsequent query returned `poweroff` in 0.63 seconds and the original single `clean` snapshot.
- The first real UI launch reached Windows startup but exceeded the host's 420-second agent-readiness deadline under heavy paging. The sample did not execute. Session `sess_2026_11_06_988fee7b` recorded `FAILED`, and normal shutdown/restore completed. Boot screenshots, VirtualBox and DHCP logs, host memory observations, and the failed result were preserved locally before retrying.
- The local, gitignored development configuration now allows 900 seconds for guest readiness. The workload deadline remains separate. The increased deadline alone is not counted as a successful run.
- The API distinguishes a valid VirtualBox state from an unavailable guest agent. Expected guest-health failures populate optional `agent_error`; management failures still populate `error` and block launch. The console provides **Retry status**, displays an active/not-ready guest truthfully, and keeps terminal lifecycle status from being overwritten by a stale Finalizing phase.
- Git checkout conversion had changed `sysmon.xml` bytes and broken manifest verification. Explicit guest-file line-ending attributes restore the existing byte contract. Both LF-default and CRLF-default fresh checkouts verify the original 13-file build `a02fad5c6023a1fc`; no guest code or build manifest was changed.

The idle management-process restart was a host recovery step, not a new automatic service-killing API. Do not terminate VirtualBox management while another VM, GUI, or harness is active. No snapshot was renamed, deleted, promoted, or replaced in this repair.

## Verification

**Passed.** The real frontend launch completed session `sess_2026_11_06_608495c5` in the control arm. The sample's SHA-256 is `1d4902a04d99e8ccbfe7085e63155955fee397449d386453f6c452ae407b8743`, with PID **5832** and exit code **0**.

| Gate | Result |
| --- | --- |
| Real UI launch, not simulation | Passed; `COMPLETED`, no session failures, terminal state survives reload. |
| Sample correlation | Two retained Sysmon records identify PID 5832 and `C:\ADAM\samples\smoke_sample.exe` within this session's time window. |
| Raw evidence | 23,091 records: 154 Sysmon, 22,853 Procmon, 84 network. No duplicate event IDs or non-null source identities. |
| Durable analysis | 1,054 semantic events and 1,054 policy decisions; zero applied mutations as expected for control. |
| Captures | All three sources verified, captured, exported, and parsed without errors; network download has a valid PCAPNG header. |
| Agreement | API metadata, SQLite session metadata, and saved `session.json` match. Raw/source totals, persisted semantic/decision counts, and JSON report metrics agree. HTML and Markdown exports also succeed. |
| UI downloads | Actual JSON report matches this completed session; downloaded network capture SHA-256 matches the retained file. |
| Final console | Launch enabled, no error banner, completed lifecycle shown, and status query returns `Off`, `clean`, not busy, no management error. |
| Cleanup and baseline | Normal guest shutdown and restore completed. Original snapshot UUID remains `4d3f4a15-5567-474f-96e5-208537a5ce4f`, with one `clean` snapshot and `currentStateModified=false`. |
| Regression suite | 546 backend tests passed, one skipped; 6 component tests and 10 browser checks passed; changed Python lint, active frontend lint, and production build passed. |
| Agent packaging | Original 13-file build verifies in checkouts with both `core.autocrlf=true` and `false`. |

The run took about **10 minutes 35 seconds including guest startup, capture export/processing, and cleanup**. It does not mean the sample itself executed for that duration. The local readiness allowance is 15 minutes; the sample workload limit in this check was 5 minutes. Startup still depends on host resources and is not guaranteed to succeed under every load. A fresh real treatment run was not part of this repair.

Local evidence is under `logs/console-launch-repair/`, which is intentionally excluded from Git: `second/live-ui-result.json`, `second/live-ui-result.png`, `final-audit.json`, `final-ui.json`, `final-sandbox-status.json`, `second-agent-version.json`, `second-boot-complete.log`, and `backend-tests-final.txt`. The browser regression checks use controlled responses for error recovery; the opt-in live test separately established real execution and retained captures. The failed first attempt and its diagnostics remain preserved alongside the successful run.
