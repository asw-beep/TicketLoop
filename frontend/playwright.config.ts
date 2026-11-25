import { defineConfig } from "@playwright/test";
import { API_PORT, API_URL, PYTHON_ENV, WEB_PORT, WEB_URL } from "./e2e/env";

/**
 * Browser tests against the real stack: Vite, the API, Postgres, Mailpit and MinIO.
 *
 *   docker compose up -d db mailpit minio     then, from frontend/:   npm run e2e
 *
 * Playwright starts an API and a dev server of its own, on their own ports and database
 * (e2e/env.ts), so it never reuses the ones you are developing against. It drives the
 * installed Google Chrome, here and on GitHub's runners, so there is no browser download.
 */
const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  workers: 1, // the tests share one database and one inbox
  retries: CI ? 1 : 0,
  reporter: CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB_URL,
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Recreates and migrates the database before it serves.
      command: `uv run --directory .. python -m scripts.e2e_api --port ${API_PORT}`,
      url: `${API_URL}/api/v1/health`,
      env: PYTHON_ENV,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT}`,
      url: WEB_URL,
      env: { API_TARGET: API_URL },
      reuseExistingServer: false,
    },
  ],
});
