import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';
const localPython=resolve('..','.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
export default defineConfig({
  testDir: './e2e', timeout: 30000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:8765', headless: true, screenshot: 'only-on-failure' },
  webServer: {command: `"${process.env.T1FX_PYTHON || (existsSync(localPython)?localPython:'python')}" e2e/server.py`, url: 'http://127.0.0.1:8765/health/live', reuseExistingServer: false, timeout: 30000},
});
