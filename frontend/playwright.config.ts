import { defineConfig } from "@playwright/test";

/**
 * Browser tests against the real stack: Vite, the API, Postgres, Mailpit and MinIO.
 *
 *   docker compose up -d db mailpit minio     then, from frontend/:   npm run e2e
 *
 * Playwright starts the API and the dev server unless they are already running. It drives
 * the installed Google Chrome, here and on GitHub's runners, so there is no browser download.
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
    baseURL: "http://localhost:5173",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run --directory .. uvicorn app.main:app --port 8000",
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: !CI,
      timeout: 60_000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: !CI,
    },
  ],
});
