import { defineConfig } from '@playwright/test';

for (const name of ['NO_PROXY', 'no_proxy']) {
  process.env[name] = [process.env[name], 'localhost', '127.0.0.1'].filter(Boolean).join(',');
}

export default defineConfig({
  testDir: './tests',
  testMatch: 'rollover.spec.js',
  workers: 1,
  timeout: 45000,
  use: { baseURL: 'http://127.0.0.1:5174', viewport: { width: 1440, height: 960 } },
  webServer: [
    {
      command: 'cd .. && .venv/bin/python -m tests.support.rollover_server',
      url: 'http://127.0.0.1:8765/api/state',
      env: { FRONTEND_ORIGINS: 'http://127.0.0.1:5174' },
    },
    {
      command: 'npx vite --host 127.0.0.1 --port 5174 --strictPort',
      url: 'http://127.0.0.1:5174',
      env: { VITE_API_URL: 'http://127.0.0.1:8765' },
    },
  ],
});
