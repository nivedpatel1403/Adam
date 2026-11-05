import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

test('live UI launch produces a durable real-VM session and retained capture files', async ({ page, request }) => {
  test.skip(process.env.ADAM_LIVE_UI_TEST !== '1', 'Set ADAM_LIVE_UI_TEST=1 to deliberately run the VM smoke sample.');
  test.setTimeout(14 * 60 * 1000);
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
  }, { timeout: 12 * 60 * 1000, intervals: [5000] }).toMatch(/COMPLETED|PARTIAL|FAILED|ABORTED/);
  const result = await (await request.get(`/api/v1/sessions/${id}/summary`)).json();
  await writeFile('../../logs/frontend-verification/20261105-industrial/live-ui-result.json', JSON.stringify(result, null, 2));
  await page.reload();
  await expect(page.locator('.session-strip')).toContainText(result.metadata.status.charAt(0) + result.metadata.status.slice(1).toLowerCase());
  await page.screenshot({ path: '../../logs/frontend-verification/20261105-industrial/live-ui-result.png', fullPage: true });
  expect(result.metadata.status, result.metadata.error || 'Real session must complete').toBe('COMPLETED');
  expect(result.metadata.metrics.raw_events).toBeGreaterThan(0);
  expect(result.artifacts.map((a: { kind: string }) => a.kind)).toEqual(expect.arrayContaining(['raw', 'sysmon', 'procmon', 'network']));
  const artifact = await request.get(`/api/v1/sessions/${id}/artifacts/network`);
  expect(artifact.ok()).toBe(true); expect((await artifact.body()).byteLength).toBeGreaterThan(0);
});
