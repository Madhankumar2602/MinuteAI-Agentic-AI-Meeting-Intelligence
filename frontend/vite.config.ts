/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API is reached through Vite's proxy, so the browser sees a
// single origin (localhost:5173) and API calls need no CORS at all. Recording
// uploads are the exception: they go straight to object storage, whose bucket
// CORS rule allows this origin (backend app/services/storage.py).
const API_TARGET = process.env.MINUTEAI_API_URL ?? "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
