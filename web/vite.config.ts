import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

const proxy = {
  '/api': { target: 'http://127.0.0.1:8420', changeOrigin: true },
  '/health': { target: 'http://127.0.0.1:8420', changeOrigin: true },
};
export default defineConfig({
  plugins: [tailwindcss()],
  resolve: { alias: { '@': new URL('./src', import.meta.url).pathname } },
  server: { host: '127.0.0.1', proxy },
  preview: { host: '127.0.0.1', proxy },
});
