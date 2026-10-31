import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

const proxy = {
  '/api': { target: 'http://127.0.0.1:8420', changeOrigin: true },
  '/health': { target: 'http://127.0.0.1:8420', changeOrigin: true },
};
export default defineConfig({
  plugins: [tailwindcss()],
  server: { host: '127.0.0.1', proxy },
  preview: { host: '127.0.0.1', proxy },
});
