import { defineConfig } from 'vite';

export default defineConfig({
  // 相对路径 base，便于部署到 GitHub Pages 任意子路径
  base: './',
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 2000,
  },
  worker: {
    format: 'es',
  },
});
