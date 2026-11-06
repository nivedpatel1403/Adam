import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

test('live UI launch produces a durable real-VM session and retained capture files', async ({ page, request }) => {
  test.skip(process.env.ADAM_LIVE_UI_TEST !== '1', 'Set ADAM_LIVE_UI_TEST=1 to deliberately run the VM smoke sample.');
  const sandbox = await (await request.get('/api/v1/sandbox/status')).json();
  const readinessSeconds = sandbox.readiness_timeout_seconds || 420;
  const terminalDeadline = (readinessSeconds + 300 + 180) * 1000;
  test.setTimeout(terminalDeadline + 60000);
  const evidence = process.env.ADAM_UI_EVIDENCE_DIR || '../../logs/frontend-verification/20261105-industrial';
  await mkdir(evidence, { recursive: true });
  await page.goto('/');
  const launch = page.getByRole('button', { name: 'Launch analysis', exact: true });
  await expect(launch).toBeEnabled();
  await launch.click();
  await page.waitForURL(/\/sessions\/sess_/, { timeout: 30000 });
  const id = page.url().split('/').at(-1)!;
  await expect(page.getByRole('heading', { name: /Session / })).toBeVisible();
  await expect.poll(async () => {
    const response = await request.get(`/api/v1/sessions/${id}/summary`);
    return (await response.json()).metadata.status;
  }, { timeout: terminalDeadline, intervals: [5000] }).toMatch(/COMPLETED|PARTIAL|FAILED|ABORTED/);
  const result = await (await request.get(`/api/v1/sessions/${id}/summary`)).json();
  await writeFile(`${evidence}/live-ui-result.json`, JSON.stringify(result, null, 2));
  await page.reload();
  await expect(page.locator('.session-strip')).toContainText(result.metadata.status.charAt(0) + result.metadata.status.slice(1).toLowerCase());
  await page.screenshot({ path: `${evidence}/live-ui-result.png`, fullPage: true });
  expect(result.metadata.status, result.metadata.error || 'Real session must complete').toBe('COMPLETED');
  expect(result.metadata.execution_exit_code).toBe(0);
  expect(result.metadata.execution_pid).toBeGreaterThan(0);
  expect(result.metadata.metrics.raw_events).toBeGreaterThan(0);
  expect(result.artifacts.map((a: { kind: string }) => a.kind)).toEqual(expect.arrayContaining(['raw', 'sysmon', 'procmon', 'network']));
  const artifact = await request.get(`/api/v1/sessions/${id}/artifacts/network`);
  expect(artifact.ok()).toBe(true); expect((await artifact.body()).byteLength).toBeGreaterThan(0);
  expect((await artifact.body()).subarray(0, 4).toString('hex')).toBe('0a0d0d0a');
  for (const source of ['sysmon', 'procmon', 'network']) {
    expect(result.metadata.source_outcomes[source].parsed).toBe(true);
    expect(result.metadata.source_outcomes[source].events_count).toBeGreaterThan(0);
  }
  const raw = await request.get(`/api/v1/sessions/${id}/artifacts/raw`);
  expect(raw.ok()).toBe(true);
  const matching = (await raw.text()).trim().split(/\r?\n/).some(line => {
    const record = JSON.parse(line);
    return record.process?.pid === result.metadata.execution_pid && /smoke_sample\.exe$/i.test(record.process?.image || '');
  });
  expect(matching, 'Retained raw evidence must identify the executed sample PID and image').toBe(true);
});
