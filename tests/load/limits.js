// The real rate limits (M12), once: 20 users searching without a pause from one address for a
// minute. Search allows 60 a minute per address; the rest must be 429 with Retry-After, and
// nothing may fail with a 5xx.
//
//   uv run python -m scripts.load_test limits      # the runner sets the real limits, then back
import http from "k6/http";
import { Counter } from "k6/metrics";
import { API, DNS, TREND_STATS, writeSummary } from "./lib.js";

const allowed = new Counter("allowed");
const limited = new Counter("limited"); // 429 with Retry-After
const serverErrors = new Counter("server_errors");
const unexpected = new Counter("unexpected");

export const options = {
  dns: DNS,
  summaryTrendStats: TREND_STATS,
  scenarios: { search: { executor: "constant-vus", vus: 20, duration: "1m" } },
  thresholds: {
    limited: ["count>0"],
    server_errors: ["count==0"],
    unexpected: ["count==0"],
    // Reported, not judged: the bucket holds 60 and refills one a second, so about 120 pass.
    allowed: ["count>=0"],
  },
};

export function setup() {
  for (const counter of [allowed, limited, serverErrors, unexpected]) counter.add(0);
}

export default function () {
  const response = http.get(`${API}/listings?city=Mumbai&limit=20`, {
    tags: { name: "search" },
    responseCallback: http.expectedStatuses(200, 429),
  });
  if (response.status === 200) allowed.add(1);
  else if (response.status === 429 && response.headers["Retry-After"]) limited.add(1);
  else if (response.status >= 500) serverErrors.add(1);
  else unexpected.add(1, { status: String(response.status) });
}

export const handleSummary = writeSummary;
