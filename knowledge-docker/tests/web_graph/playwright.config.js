import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: 'graph.spec.js', workers: 1, fullyParallel: false,
  timeout: 900_000, expect: { timeout: 15_000 },
  outputDir: '/artifacts/web-graph/results',
  reporter: [['list'], ['json', { outputFile: '/artifacts/web-graph/report.json' }]],
  use: {
    browserName: 'chromium', viewport: { width: 1440, height: 1000 }, timezoneId: 'UTC',
    actionTimeout: 15_000, navigationTimeout: 30_000,
    trace: 'off', video: 'off', screenshot: 'off',
  },
});
