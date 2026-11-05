import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { ArchiveItem } from '../../src/console/api';

const evidence = '../../logs/frontend-verification/20261105-industrial';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', error => { throw error; });
});

test('console uses genuine samples, accessible controls, and the reference layout', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Console', exact: true })).toBeVisible();
  await expect(page.getByLabel('Sample', { exact: true })).toContainText('smoke_sample.exe');
  await expect(page.getByLabel('Analysis arm')).toHaveValue('control');
  const sandbox = await (await page.request.get('/api/v1/sandbox/status')).json();
  if (sandbox.error) await expect(page.getByRole('button', { name: 'Launch analysis', exact: true })).toBeDisabled();
  else await expect(page.getByRole('button', { name: 'Launch analysis', exact: true })).toBeEnabled();
  await page.screenshot({ path: `${evidence}/console-desktop.png`, fullPage: true });
  const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('real completed and failed sessions display canonical counts and failures', async ({ page, request }) => {
  const response = await request.get('/api/v1/console/sessions?limit=100&mode=live');
  const archive: { items: ArchiveItem[] } = await response.json();
  const completed = archive.items.find(s => s.metadata.status === 'COMPLETED' && s.metadata.metrics.raw_events > 0)!;
  const failed = archive.items.find(s => s.metadata.status === 'FAILED')!;
  expect(completed).toBeTruthy(); expect(failed).toBeTruthy();
  await page.goto(`/sessions/${completed.metadata.session_id}`);
  await expect(page.locator('.session-strip')).toContainText('Completed');
  await expect(page.locator('.metric').filter({ hasText: 'Raw records' }).locator('strong')).toHaveText(completed.metadata.metrics.raw_events.toLocaleString());
  await expect(page.getByRole('heading', { name: 'Capture sources' })).toBeVisible();
  await page.screenshot({ path: `${evidence}/session-desktop.png`, fullPage: true });
  const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  await page.getByRole('button', { name: 'Decisions', exact: true }).click();
  await expect(page.getByRole('region', { name: 'decisions timeline' })).toBeVisible();
  await page.getByRole('button', { name: 'Inspect', exact: false }).first().click();
  await expect(page.getByRole('dialog', { name: 'Recorded event details' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await page.goto(`/sessions/${failed.metadata.session_id}`);
  await expect(page.getByRole('alert').filter({ hasText: 'Analysis did not complete' })).toBeVisible();
  await expect(page.getByText('Recorded failures', { exact: false })).toBeVisible();
  await page.screenshot({ path: `${evidence}/session-failed.png`, fullPage: true });
});

test('reset confirmation cancels safely and restores focus', async ({ page }) => {
  let resetRequests = 0;
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/sandbox/reset')) resetRequests++; });
  await page.route('**/api/v1/sandbox/status', route => route.fulfill({ json: {vm_state:'Off',agent_healthy:false,current_snapshot:'clean',vm_name:'Windows',profile:'Windows',busy:false,error:null,readiness_timeout_seconds:420} }));
  await page.goto('/');
  const reset = page.getByRole('button', { name: 'Restore baseline', exact: true });
  await expect(reset).toBeEnabled(); await reset.click();
  await expect(page.getByRole('dialog', { name: 'Restore the clean baseline?' })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(reset).toBeFocused(); expect(resetRequests).toBe(0);
});

test('mobile navigation, reduced motion, and simulation stay usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Console', exact: true })).toBeVisible();
  await page.screenshot({ path: `${evidence}/console-mobile.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Toggle navigation' }).click();
  await page.getByRole('link', { name: 'Simulation', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Simulation', exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('generated events');
  await page.screenshot({ path: `${evidence}/simulation-mobile.png`, fullPage: true });
  const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
});

test('comparison clearly represents actual paired simulation results', async ({ page, request }) => {
  const bytes = Buffer.from('ADAM frontend deterministic simulation seed');
  const experimentResponse = await request.post('/api/v1/experiments/run', { multipart: { file: { name: 'ui-seed.bin', mimeType: 'application/octet-stream', buffer: bytes } } });
  expect(experimentResponse.ok()).toBe(true);
  const experiment = await experimentResponse.json();
  await page.goto(`/experiments/${experiment.experiment_id}`);
  await expect(page.getByRole('heading', { name: 'Recorded outcomes' })).toBeVisible({ timeout: 45000 });
  await expect(page.getByRole('region', { name: 'Control and treatment comparison' })).toContainText('Control');
  await expect(page.getByRole('region', { name: 'Control and treatment comparison' })).toContainText('Treatment');
  await page.screenshot({ path: `${evidence}/comparison-desktop.png`, fullPage: true });
});

test('a failed launch stays visible without automatic resubmission', async ({ page }) => {
  await page.route('**/api/v1/sandbox/status', route => route.fulfill({ json: {vm_state:'Off',agent_healthy:false,current_snapshot:'clean',vm_name:'Windows',profile:'Windows',busy:false,error:null,readiness_timeout_seconds:420} }));
  let attempts = 0;
  await page.route('**/api/v1/sessions/live?**', async route => { attempts++; await route.fulfill({ status: 409, json: { detail: 'Controlled VM operation conflict' } }); });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Launch analysis', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Launch analysis', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Controlled VM operation conflict');
  await expect(page.getByLabel('Sample', { exact: true })).toHaveValue('smoke');
  expect(attempts).toBe(1);
});

test('archive search has an honest empty state and recovers', async ({ page }) => {
  await page.goto('/sessions');
  await expect(page.getByRole('region', { name: 'Analysis sessions' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Search sessions' }).fill('no-such-adam-sample-frontend-check');
  await expect(page.getByRole('heading', { name: 'No matching sessions' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Search sessions' }).fill('');
  await expect(page.getByRole('region', { name: 'Analysis sessions' })).toBeVisible();
});

test('retained evidence downloads and a disappeared file shows a recoverable error', async ({ page, request }) => {
  const archive = await (await request.get('/api/v1/console/sessions?limit=100&mode=live')).json();
  const session = archive.items.find((s: ArchiveItem) => s.metadata.status === 'COMPLETED');
  const id = session.metadata.session_id;
  await page.goto(`/sessions/${id}`);
  const raw = page.getByRole('link').filter({ hasText: 'raw.jsonl' });
  await expect(raw).toBeVisible();
  const downloading = page.waitForEvent('download');
  await raw.click();
  expect((await downloading).suggestedFilename()).toBe('raw.jsonl');
  await page.route(`**/api/v1/sessions/${id}/artifacts/raw`, route => route.fulfill({ status: 404, json: { detail: 'Evidence no longer retained' } }));
  await raw.click();
  await expect(page.getByRole('alert').filter({ hasText: 'Download unavailable (404)' })).toBeVisible();
  await expect(page.locator('.session-strip')).toContainText('Completed');
});
