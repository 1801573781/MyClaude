import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: 1,
  workers: 1, // Electron 单实例（SQLite 单实例锁）
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-e2e',
      use: {
        // Electron 应用由测试内 _electron.launch 拉起
      },
    },
  ],
});
