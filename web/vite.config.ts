import { defineConfig } from 'vite';

const proxy = {
  '/api': { target: 'http://127.0.0.1:8420', changeOrigin: true },
  '/health': { target: 'http://127.0.0.1:8420', changeOrigin: true },
};
export default defineConfig({
  server: { host: '127.0.0.1', proxy },
  preview: { host: '127.0.0.1', proxy },
});
