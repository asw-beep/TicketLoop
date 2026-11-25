/**
 * Where the browser tests run: their own database, API and dev server, so they never meet the
 * rows or the servers of someone developing on the same laptop.
 */
const base = new URL(
  process.env.DATABASE_URL ?? "postgresql+psycopg://ticketloop:ticketloop@localhost:5432/x",
);
base.pathname = "/ticketloop_e2e";

export const API_PORT = 8001;
export const WEB_PORT = 5174;
export const WEB_URL = `http://localhost:${WEB_PORT}`;
export const API_URL = `http://127.0.0.1:${API_PORT}`;

/** For every Python process a browser test starts. */
export const PYTHON_ENV = {
  DATABASE_URL: base.href,
  FRONTEND_URL: WEB_URL, // reset emails link here
  LOG_JSON: "false",
  LOG_LEVEL: "WARNING",
};
