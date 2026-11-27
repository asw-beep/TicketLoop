// What the M12 k6 scripts share. The data is scripts/seed_discovery.py's: 2,000 events,
// 100,000 listings, and 1,000 buyers who all have one password.
import { fail } from "k6";
import http from "k6/http";

export const API = `${__ENV.BASE_URL || "http://api:8000"}/api/v1`;
export const FAKEPAY = __ENV.FAKEPAY_URL || "http://fakepay:8090";
export const JSON_HEADERS = { "Content-Type": "application/json" };
// p99 is not in k6's default list.
export const TREND_STATS = ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"];
// Spread connections over the four API containers: Docker's DNS gives one address each.
export const DNS = { ttl: "0", select: "random", policy: "onlyIPv4" };

const PASSWORD = "load test password 123";
const AT_ONCE = 10; // Argon2id takes 64 MiB a check; ten at a time keeps the API's memory in hand

export function buyerEmail(index) {
  return `buyer-${String(index).padStart(4, "0")}@perf.example.in`;
}

/** Access tokens for the first `count` seeded buyers. */
export function logIn(count) {
  const tokens = [];
  for (let start = 0; start < count; start += AT_ONCE) {
    const batch = [];
    for (let index = start; index < Math.min(count, start + AT_ONCE); index++) {
      const body = JSON.stringify({ email: buyerEmail(index), password: PASSWORD });
      batch.push(["POST", `${API}/auth/login`, body, { headers: JSON_HEADERS, tags: { name: "login" } }]);
    }
    for (const response of http.batch(batch)) {
      if (response.status !== 200) fail(`login answered ${response.status}: ${response.body}`);
      tokens.push(response.json("access_token"));
    }
  }
  return tokens;
}

/** Ids of up to `max` published events, from the public list. */
export function eventIds(max) {
  const ids = [];
  let cursor = null;
  while (ids.length < max) {
    const after = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = http.get(`${API}/events?limit=100${after}`, { tags: { name: "setup" } });
    if (page.status !== 200) fail(`events answered ${page.status}: ${page.body}`);
    for (const event of page.json("items")) ids.push(event.id);
    cursor = page.json("next_cursor");
    if (!cursor) break;
  }
  return ids.slice(0, max);
}

export function authorized(token, headers = {}) {
  return { Authorization: `Bearer ${token}`, ...JSON_HEADERS, ...headers };
}

export function pick(items) {
  return items[Math.floor(Math.random() * items.length)];
}

/** The summary, as JSON, where the runner reads it; without the buyers' tokens. */
export function writeSummary(data) {
  if (data.setup_data) delete data.setup_data.tokens;
  return { [`/results/${__ENV.NAME}.json`]: JSON.stringify(data, null, 1) };
}

/** The type of a problem+json answer, without its prefix: "listing-not-available". */
export function problem(response) {
  try {
    return String(response.json("type")).replace("urn:ticketloop:problem:", "");
  } catch (_) {
    return "";
  }
}
