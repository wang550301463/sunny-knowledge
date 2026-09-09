import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: '*.spec.js', workers: 1, fullyParallel: false,
  timeout: 120_000, expect: { timeout: 15_000 },
  outputDir: '/artifacts/browser-results',
  reporter: [['list'], ['json', { outputFile: '/artifacts/browser-report.json' }]],
  use: {
    browserName: 'chromium', viewport: { width: 1440, height: 960 },
    actionTimeout: 15_000, navigationTimeout: 30_000,
    // Credentials are supplied at runtime. Never record auth bodies or token storage.
    trace: 'off', video: 'off', screenshot: 'off',
  },
});
