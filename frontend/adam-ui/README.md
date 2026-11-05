# ADAM analysis console

React/TypeScript frontend for the existing local ADAM API. The visual contract is [DESIGN.md](../../DESIGN.md). It uses locally bundled fonts, gray instrument panels, dark status readouts, orange actions, and responsive navigation.

## Run locally

From the repository root, start the API in one PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn adam.api.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd "C:\Works\Major Project\ADAM-code\Nived\Adam\frontend\adam-ui"
npm ci
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173. The development server proxies `/api` to port 8000. Keep the API and UI on the local host. For a separately hosted API, set `VITE_API_BASE_URL` before building. `npm run build` writes `dist`; deploying static output requires an API reverse proxy and history fallback for application routes.

## Use

- **Console:** select the genuine built-in `smoke_sample.exe` or upload a real file (up to 64 MiB). Control is the default. Treatment must be selected explicitly. Launch returns the real Runner session ID.
- **Sessions:** search/filter the database archive, inspect paginated behavioral events, decisions, mutations, source capture outcomes, and structured failures. Export retained capture files and reports. Missing evidence stays missing.
- **Simulation:** generate deterministic synthetic sessions or paired experiments. These are explicitly labeled and do not execute uploaded seeds in the VM. Synthetic replay is also labeled separately.
- **Comparison:** inspect actual control/treatment records and observed deltas; no malware verdict is invented.
- **Restore baseline:** requires confirmation and uses normal shutdown. Failure stops restoration. The frontend never promotes or deletes snapshots.

Live sessions use bounded SSE updates plus canonical polling and durable database results. Workload timeout is separate from the configured guest-readiness timeout. VM/agent failures are displayed; startup reliability remains a separate backend task. Only one API-owned VM operation is allowed at a time. Do not run an independent CLI/harness against the same VM concurrently.

## Verify

```powershell
npm test
npm run lint
npm run build
npx playwright install chromium
npm run test:browser -- tests/browser/console.spec.ts
```

Browser tests require both servers running and the existing completed/failed real sessions in the local database. Reset-cancel and launch-conflict checks use controlled status responses and never change the VM. Comparison verification creates clearly marked synthetic records.

The deliberate real-VM check is opt-in and must run without another VM owner:

```powershell
$env:ADAM_LIVE_UI_TEST='1'
npm run test:browser -- tests/browser/live.spec.ts
```

It launches the genuine benign built-in sample in control mode and requires completed metadata plus retained captures. A readiness/status failure is a failed live check, never a successful simulation substitute. See [frontend verification](../../docs/frontend-verification.md) for recorded results and limitations.
