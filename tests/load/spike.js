// The spike (M12): 1,000 buyers reserve the same listing at the same moment. Exactly one may
// win; every other buyer must get a clean 409 listing-not-available, and nobody a 5xx.
//
//   uv run python -m scripts.load_test spike
import http from "k6/http";
import { Counter } from "k6/metrics";
import { API, DNS, TREND_STATS, authorized, logIn, problem, writeSummary } from "./lib.js";

const BUYERS = Number(__ENV.BUYERS || 1000);

const reserved = new Counter("reserved");
const refused = new Counter("refused"); // 409 listing-not-available
const serverErrors = new Counter("server_errors");
const unexpected = new Counter("unexpected"); // anything else, a 409 of another kind included

export const options = {
  dns: DNS,
  setupTimeout: "5m",
  summaryTrendStats: TREND_STATS,
  scenarios: {
    // Every VU is started before the first one sends, then all send once.
    spike: { executor: "per-vu-iterations", vus: BUYERS, iterations: 1, maxDuration: "2m" },
  },
  thresholds: {
    reserved: ["count==1"],
    refused: [`count==${BUYERS - 1}`],
    server_errors: ["count==0"],
    unexpected: ["count==0"],
    // Reported, not judged: how long losing took.
    "http_req_duration{name:reserve}": ["max>=0"],
  },
};

export function setup() {
  // A counter nothing adds to has no value, and a threshold on it is never checked.
  for (const counter of [reserved, refused, serverErrors, unexpected]) counter.add(0);
  const tokens = logIn(BUYERS);
  const page = http.get(`${API}/listings?limit=1`, { tags: { name: "setup" } });
  const [listing] = page.json("items");
  return { tokens, listing_id: listing.id };
}

export default function (data) {
  const response = http.post(`${API}/listings/${data.listing_id}/reservations`, null, {
    headers: authorized(data.tokens[__VU - 1]),
    tags: { name: "reserve" },
    responseCallback: http.expectedStatuses(201, 409),
  });
  if (response.status === 201) reserved.add(1);
  else if (response.status === 409 && problem(response) === "listing-not-available") refused.add(1);
  else if (response.status >= 500) serverErrors.add(1);
  else unexpected.add(1, { status: String(response.status), problem: problem(response) });
}

export const handleSummary = writeSummary;
