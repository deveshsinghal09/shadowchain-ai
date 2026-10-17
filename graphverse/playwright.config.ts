import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'tests',
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  use: {
    baseURL: 'http://127.0.0.1:3100',
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      args: ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    },
    screenshot: 'only-on-failure',
  },
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
});
