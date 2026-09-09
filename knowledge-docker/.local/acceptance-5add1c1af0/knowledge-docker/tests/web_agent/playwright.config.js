import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: process.env.WEB_CHANNEL_RESULT_ONLY === 'true' ? 'channel-result.spec.js' : 'agent.spec.js',
  workers: 1, fullyParallel: false,
  timeout: 180_000, expect: { timeout: 15_000 },
  outputDir: process.env.WEB_CHANNEL_RESULT_ONLY === 'true' ? '/artifacts/web-agent/channel-results' : '/artifacts/web-agent/results',
  reporter: [['list'], ['json', { outputFile: process.env.WEB_CHANNEL_RESULT_ONLY === 'true' ? '/artifacts/web-agent/channel-report.json' : '/artifacts/web-agent/report.json' }]],
  use: {
    browserName: 'chromium', viewport: { width: 1440, height: 1000 },
    actionTimeout: 15_000, navigationTimeout: 30_000,
    // No request bodies, token storage, passwords or binding proofs in artifacts.
    trace: 'off', video: 'off', screenshot: 'off',
  },
});
