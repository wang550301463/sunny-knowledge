import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: 'onboarding.spec.js', workers: 1, fullyParallel: false,
  timeout: 360_000, expect: { timeout: 15_000 },
  outputDir: '/artifacts/web-onboarding/results',
  reporter: [['list'], ['json', { outputFile: '/artifacts/web-onboarding/report.json' }]],
  use: {
    browserName: 'chromium', viewport: { width: 1440, height: 1000 },
    actionTimeout: 15_000, navigationTimeout: 30_000,
    // No credentials, authorization headers, callback codes or private storage in artifacts.
    trace: 'off', video: 'off', screenshot: 'off',
  },
});
