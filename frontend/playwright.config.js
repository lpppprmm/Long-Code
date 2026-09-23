import { defineConfig } from '@playwright/test';

// The local preview must remain reachable when the shell uses an HTTP proxy.
for (const name of ['NO_PROXY', 'no_proxy']) {
  process.env[name] = [process.env[name], 'localhost', '127.0.0.1'].filter(Boolean).join(',');
}

export default defineConfig({
  testDir: './tests',
  testIgnore: 'rollover.spec.js',
  fullyParallel: true,
  use: { baseURL: 'http://127.0.0.1:5173', viewport: { width: 1440, height: 960 } },
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
  },
});
