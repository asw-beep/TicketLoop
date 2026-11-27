// The load test (M12): VUS users, 80% browsing and 20% buying, ramped up over 30 s, held for
// HOLD, ramped down over 15 s. Targets from NFR-03: reads under 300 ms p95, reservations under
// 500 ms p95, errors under 1%.
//
//   uv run python -m scripts.load_test load --vus 100      # and 500, 1000
import { check, sleep } from "k6";
import http from "k6/http";
import { Counter, Trend } from "k6/metrics";
import {
  API, DNS, FAKEPAY, TREND_STATS, authorized, eventIds, logIn, pick, problem, writeSummary,
} from "./lib.js";

const VUS = Number(__ENV.VUS || 100);
const HOLD = __ENV.HOLD || "3m";
const BUYING = Math.max(1, Math.round(VUS * 0.2));
const BROWSING = VUS - BUYING;
const CITIES = ["Mumbai", "Bengaluru", "Delhi", "Chennai", "Hyderabad", "Kolkata", "Pune"];
const SORTS = ["price", "-price", "created_at", "-created_at"];
const PAID_OR_LATER = ["PAID", "TRANSFER_PENDING", "COMPLETED", "TRANSFER_FAILED"];
const GAVE_UP = ["PAYMENT_FAILED", "CANCELLED"];

const timeToPaid = new Trend("time_to_paid", true); // fakepay told to pay, until the order says PAID
const purchases = new Counter("purchases");
const taken = new Counter("taken"); // someone else held it first: a 409, not an error
const unpaid = new Counter("unpaid"); // not PAID 30 s after paying

function stages(target) {
  return [
    { duration: "30s", target },
    { duration: HOLD, target },
    { duration: "15s", target: 0 },
  ];
}

export const options = {
  dns: DNS,
  setupTimeout: "5m",
  summaryTrendStats: TREND_STATS,
  scenarios: {
    browse: { executor: "ramping-vus", exec: "browse", stages: stages(BROWSING), gracefulRampDown: "10s" },
    buy: { executor: "ramping-vus", exec: "buy", stages: stages(BUYING), gracefulRampDown: "40s" },
  },
  thresholds: {
    "http_req_duration{kind:read}": ["p(95)<300"],
    "http_req_duration{name:reserve}": ["p(95)<500"],
    http_req_failed: ["rate<0.01"],
    // Reported, not judged: they say where time goes.
    "http_req_duration{name:search}": ["max>=0"],
    "http_req_duration{name:listing}": ["max>=0"],
    "http_req_duration{name:event}": ["max>=0"],
    "http_req_duration{name:order}": ["max>=0"],
    "http_req_duration{name:intent}": ["max>=0"],
    time_to_paid: ["max>=0"],
  },
};

export function setup() {
  for (const counter of [purchases, taken, unpaid]) counter.add(0);
  // One buyer per user that might buy: VU numbers run across both scenarios.
  return { tokens: logIn(Math.min(VUS, 1000)), events: eventIds(2000) };
}

export function browse() {
  const city = pick(CITIES);
  const search = http.get(`${API}/listings?city=${city}&sort=${pick(SORTS)}&limit=20`, {
    tags: { name: "search", kind: "read" },
  });
  check(search, { "search answered": (r) => r.status === 200 });
  const items = search.status === 200 ? search.json("items") : [];
  if (items.length > 0) {
    const listing = pick(items);
    const page = http.get(`${API}/listings/${listing.id}`, { tags: { name: "listing", kind: "read" } });
    check(page, { "listing answered": (r) => r.status === 200 });
    const event = http.get(`${API}/events/${listing.event.id}`, { tags: { name: "event", kind: "read" } });
    check(event, { "event answered": (r) => r.status === 200 });
  }
  sleep(1 + Math.random());
}

export function buy(data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const tickets = http.get(`${API}/events/${pick(data.events)}/listings?limit=20`, {
    tags: { name: "event-listings", kind: "read" },
  });
  const items = tickets.status === 200 ? tickets.json("items") : [];
  if (items.length === 0) {
    sleep(1);
    return;
  }
  const hold = http.post(`${API}/listings/${pick(items).id}/reservations`, null, {
    headers: authorized(token),
    tags: { name: "reserve" },
    responseCallback: http.expectedStatuses({ min: 200, max: 299 }, 409),
  });
  if (hold.status === 409 && problem(hold) === "listing-not-available") {
    taken.add(1);
    sleep(1);
    return;
  }
  if (!check(hold, { "reserved": (r) => r.status === 201 })) return;

  const key = () => `${__VU}-${__ITER}-${Date.now()}-${Math.random()}`;
  const order = http.post(`${API}/orders`, JSON.stringify({ reservation_id: hold.json("id") }), {
    headers: authorized(token, { "Idempotency-Key": key() }),
    tags: { name: "order" },
  });
  if (!check(order, { "ordered": (r) => r.status === 201 })) return;
  const orderId = order.json("id");
  const intent = http.post(`${API}/payments/intents`, JSON.stringify({ order_id: orderId }), {
    headers: authorized(token, { "Idempotency-Key": key() }),
    tags: { name: "intent" },
  });
  if (!check(intent, { "payment started": (r) => r.status === 201 })) return;

  const paying = Date.now();
  const outcome = http.post(
    `${FAKEPAY}/v1/intents/${intent.json("provider_transaction_id")}/outcome`,
    JSON.stringify({ outcome: "succeed" }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "fakepay" } },
  );
  if (!check(outcome, { "paid at fakepay": (r) => r.status === 200 })) return;
  for (let attempt = 0; attempt < 60; attempt++) {
    sleep(0.5);
    const status = http.get(`${API}/orders/${orderId}`, {
      headers: authorized(token),
      tags: { name: "order-status" },
    }).json("status");
    if (PAID_OR_LATER.includes(status)) {
      timeToPaid.add(Date.now() - paying);
      purchases.add(1);
      sleep(1);
      return;
    }
    if (GAVE_UP.includes(status)) break;
  }
  unpaid.add(1);
}

export const handleSummary = writeSummary;
