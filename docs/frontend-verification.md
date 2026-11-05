# ADAM frontend implementation and verification

Verified on 2026-11-06 in `C:\Works\Major Project\ADAM-code\Nived\Adam`.
Base revision: `94ad6530415bf607ddadd8011493ad3aecd2e52a`.
Implementation is an uncommitted working-tree change; this is not a baseline-promotion acceptance record.

## Implemented

- React/TypeScript Console, Sessions, Simulation, and Comparison screens follow the supplied design references: gray chassis, light raised panels, dark status readouts, restrained orange actions, local fonts, responsive navigation, keyboard focus, and reduced-motion support. [DESIGN.md](../DESIGN.md) records the contract.
- Live launch stages actual uploaded bytes or selects the genuine allowlisted `samples/smoke_sample.exe`. Control is the default. The API allocates the Runner session ID before returning HTTP 202; the UI navigates to that ID.
- An API-owned operation lock rejects overlapping live launch/reset requests. Baseline restore confirms intent, requires normal shutdown, and stops on failure. No snapshot promotion, rename, deletion, or VDI manipulation was performed.
- Bounded per-session SSE queues provide updates and resynchronization signals. Paginated database reads supply canonical event, decision, and mutation records. Terminal results are announced after Runner finalization. Intermediate counts are labeled provisional.
- Complete recorded ATT&CK mappings, actual indicators, capture-source outcomes, structured failures, retained artifact downloads, and report exports are connected. Missing files produce visible errors. Lifecycle displays recorded start/status/end information without fabricating intermediate transitions.
- Simulation and replay are explicitly synthetic. Paired comparisons use actual control/treatment session IDs. Simulation completion waits for downstream processing and bounded persistence attempts.
- Status queries have a five-second VBoxManage command deadline and reap their own timed-out/cancelled query subprocess. Existing Runner/CLI deadlines remain unchanged. Guest readiness remains configured at 420 seconds, separate from workload timeout.

## Results

| Check | Outcome | Evidence |
| --- | --- | --- |
| Backend unit/integration suite | 543 passed, 1 skipped | [output](../logs/frontend-verification/20261105-industrial/backend-tests.txt) |
| Focused console/timeout/VM-binding regressions | 13 passed | `tests/unit/test_console_api.py`, `test_console_vbox_timeout.py`, `test_phase1_vbox_binding.py` |
| Frontend component tests | 5 passed | `frontend/adam-ui/src/console/console.test.tsx` |
| Browser flows | 8 passed | [full browser report](../logs/frontend-verification/20261105-industrial/browser-eight-checks.json) |
| Final affected-screen browser checks | 3 passed | [repeat report](../logs/frontend-verification/20261105-industrial/browser-results.json) |
| Accessibility | No axe WCAG 2 A/AA and 2.1 AA violations on tested Console, real completed-session, and mobile Simulation screens | Browser assertions; not a whole-product accessibility certification |
| Mobile overflow, modal Escape, reset-cancel focus restoration | Passed | Browser assertions |
| Real retained-file download and disappeared-file handling | Passed | Actual raw download; controlled 404 verifies inline recovery |
| Ruff on changed Python files; active frontend lint | Passed | [Ruff](../logs/frontend-verification/20261105-industrial/ruff.txt); frontend lint command |
| TypeScript and production build | Passed | `npm run build` |
| Frontend dependency tree | Consistent | `npm ls --depth=0` |
| Fresh UI-launched benign VM run | **Blocked before launch** | [blocker record](../logs/frontend-verification/20261105-industrial/live-check-blocker.json), [API diagnostics](../logs/frontend-verification/20261105-industrial/api.err) |

The eight browser checks cover sample selection/default arm, genuine stored completed/failed sessions and canonical counts, reset cancellation, mobile navigation, real paired synthetic comparison, controlled launch conflict with no automatic resubmission, archive search/empty recovery, and artifact download/error handling. Reset-cancel and launch-conflict tests supply controlled status responses; they do not prove live VM health. Browser comparison checks intentionally create labeled synthetic sessions in the local database.

## Visual evidence

- [Desktop Console](../logs/frontend-verification/20261105-industrial/console-desktop.png)
- [Real recorded session](../logs/frontend-verification/20261105-industrial/session-desktop.png)
- [Recorded failed session](../logs/frontend-verification/20261105-industrial/session-failed.png)
- [Mobile Console](../logs/frontend-verification/20261105-industrial/console-mobile.png)
- [Mobile Simulation](../logs/frontend-verification/20261105-industrial/simulation-mobile.png)
- [Paired comparison](../logs/frontend-verification/20261105-industrial/comparison-desktop.png)

Screenshots were inspected for hierarchy, panel spacing, readable evidence, and responsive behavior. Initial low-contrast labels and mobile overflow were fixed and the affected checks repeated. Native CUA was unavailable in this session; Chromium Playwright supplied the browser inspection and screenshots.

## Remaining limitation and handover

VirtualBox initially answered read-only status queries, then stopped responding to `showvminfo Windows --machinereadable`. The deliberate live browser test could not enable Launch and did not start a sample or create a new live session. Bounded subsequent status probes returned an explicit timeout, while stored results and synthetic flows remained available. Recorded successful sessions shown by the frontend are historical real captures, not evidence of a successful new detonation today.

Startup/VirtualBox reliability remains deferred. A fresh VM launch, normal shutdown, and final powered-off/pristine health have **not** been verified in this frontend task. No force stop, snapshot repair, or promotion was attempted. Retry the opt-in live browser test only after the host's VirtualBox state queries work and no CLI/harness owns the VM.

The legacy Streamlit application remains available; it was not redesigned or reverified here. Deployment behind a reverse proxy, authentication, other browsers, and a new real treatment run are untested. See the [frontend README](../frontend/adam-ui/README.md) for exact local startup and verification commands. Evidence lives in the local ignored logs directory; source and tests remain reviewable in the working tree.
