import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: 'lifecycle.spec.js', workers: 1, fullyParallel: false,
  timeout: 240_000, expect: { timeout: 15_000 },
  outputDir: '/artifacts/web-lifecycle/results',
  reporter: [['list'], ['json', { outputFile: '/artifacts/web-lifecycle/report.json' }]],
  use: {
    browserName: 'chromium', viewport: { width: 1440, height: 1000 },
    actionTimeout: 15_000, navigationTimeout: 30_000,
    trace: 'off', video: 'off', screenshot: 'off',
  },
});
