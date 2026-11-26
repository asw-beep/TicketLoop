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

export const FAKEPAY_PORT = 8091;
export const FAKEPAY_URL = `http://localhost:${FAKEPAY_PORT}`;

/**
 * fakepay signs its webhooks with this and the API checks them with it. Set here, so that a
 * secret of your own in the environment or in .env cannot make the two disagree.
 */
const WEBHOOK_SECRET = "local-development-only-fakepay-secret-do-not-deploy";

/** fakepay, the pretend payment gateway, with its own memory and a short "late". */
export const FAKEPAY_ENV = {
  FAKEPAY_WEBHOOK_SECRET: WEBHOOK_SECRET,
  FAKEPAY_PUBLIC_URL: FAKEPAY_URL,
  FAKEPAY_WEBHOOK_URL: `${API_URL}/api/v1/payments/webhooks/fakepay`,
  FAKEPAY_DELAY_SECONDS: "4",
};

/** For every Python process a browser test starts. */
export const PYTHON_ENV = {
  DATABASE_URL: base.href,
  FRONTEND_URL: WEB_URL, // reset emails link here, and fakepay sends buyers back here
  FAKEPAY_URL,
  FAKEPAY_WEBHOOK_SECRET: WEBHOOK_SECRET,
  // The worker and relay the API starts use queues of their own, so a development worker on
  // the same RabbitMQ never takes the browser tests' messages.
  QUEUE_PREFIX: "e2e-",
  // A Redis database of their own, emptied when the API starts. The cache stays on, so the
  // browser tests also show it never serves a page a change has made stale. The limits are
  // raised out of the way: every test comes from one address, and the admin logs in often.
  REDIS_URL: "redis://localhost:6379/1",
  RATE_LIMIT_LOGIN_PER_MINUTE: "10000",
  RATE_LIMIT_RESERVE_PER_MINUTE: "10000",
  RATE_LIMIT_PAYMENT_PER_MINUTE: "10000",
  RATE_LIMIT_SEARCH_PER_MINUTE: "10000",
  LOG_JSON: "false",
  LOG_LEVEL: "WARNING",
};
