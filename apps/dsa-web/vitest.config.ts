import { configDefaults, defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
    // CI 慢机上个别异步断言仍会抖动；仅在 CI 允许重试，失败信息仍会保留。
    retry: process.env.CI ? 2 : 0,
    exclude: [...configDefaults.exclude, 'e2e/**', 'playwright.config.ts'],
  },
});
