import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', timeout: 30000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:8765', headless: true, screenshot: 'only-on-failure' },
  webServer: {command: `${process.env.T1FX_PYTHON || '../.venv/bin/python'} e2e/server.py`, url: 'http://127.0.0.1:8765/health/live', reuseExistingServer: false, timeout: 30000},
});
