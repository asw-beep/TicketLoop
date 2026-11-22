/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server forwards /api to the backend, so the browser sees one origin
// (localhost:5173) for the pages and the API. That is why there is no CORS setup yet, and why
// the SameSite=Strict refresh cookie reaches /api/v1/auth: to the browser it is same-site.
const API_TARGET = process.env.API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true, // reset emails link to :5173, so never quietly move to another port
    proxy: { "/api": { target: API_TARGET, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
