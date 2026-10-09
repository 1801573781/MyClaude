import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],
  base: './',
  // workspace 包发布物为 CommonJS（主进程 require 兼容），真实路径不在 node_modules 内，
  // vite build 的 commonjs 插件默认不转译；dev 的 optimizeDeps 缓存又会随 dist 变更失效。
  // 统一 alias 到 TS 源码，由 esbuild/rollup 直接编译（monorepo 标准做法）。
  resolve: {
    alias: {
      '@wdbuddy/shared-types': fileURLToPath(
        new URL('../../packages/shared-types/src/index.ts', import.meta.url)
      ),
      '@wdbuddy/ui': fileURLToPath(new URL('../../packages/ui/src/index.ts', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    target: 'chrome120',
  },
});
