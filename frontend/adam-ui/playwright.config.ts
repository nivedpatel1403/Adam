import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser', fullyParallel: false, workers: 1, retries: 0, timeout: 60000,
  expect: { timeout: 15000 },
  use: { baseURL: 'http://127.0.0.1:5173', viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  outputDir: '../../logs/frontend-verification/20261105-industrial/browser-results',
  reporter: [['list'], ['json', { outputFile: '../../logs/frontend-verification/20261105-industrial/browser-results.json' }]],
});
