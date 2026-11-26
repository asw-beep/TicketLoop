import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { ApiError } from "../api/problem";
import {
  checkoutProblem,
  checkoutUrl,
  isUncertain,
  isWorthRetrying,
  newKey,
  orderFor,
  useClickKeys,
} from "./checkout";

function problem(status: number, code: string, extra: object = {}): ApiError {
  return new ApiError({ type: `urn:ticketloop:problem:${code}`, title: code, status, ...extra });
}

const OFFLINE = new TypeError("Failed to fetch");
const IN_PROGRESS = problem(409, "idempotency-key-in-progress");
const GATEWAY_DOWN = problem(503, "payment-gateway-unavailable");
const ENDED = problem(409, "reservation-not-payable");
// A certain answer, though a 5xx: the gateway said no, and the hold has ended.
const REFUSED = problem(502, "payment-refused");

describe("newKey", () => {
  it("is different every time and fits the header", () => {
    const keys = new Set(Array.from({ length: 50 }, newKey));
    expect(keys.size).toBe(50);
    for (const key of keys) expect(key).toMatch(/^[0-9a-f]{32}$/);
  });
});

describe("which failures leave the click uncertain", () => {
  it.each([
    [OFFLINE, true],
    [IN_PROGRESS, true],
    [GATEWAY_DOWN, true],
    [problem(500, "internal-error"), true],
    [ENDED, false],
    [REFUSED, false],
    [problem(422, "idempotency-key-reused"), false],
    [null, false],
  ])("%s: %s", (error, uncertain) => {
    expect(isUncertain(error)).toBe(uncertain);
  });

  it("retries by itself a few times, but not while the payment service is down", () => {
    expect(isWorthRetrying(0, OFFLINE)).toBe(true);
    expect(isWorthRetrying(2, IN_PROGRESS)).toBe(true);
    expect(isWorthRetrying(3, OFFLINE)).toBe(false);
    expect(isWorthRetrying(0, GATEWAY_DOWN)).toBe(false);
    expect(isWorthRetrying(0, ENDED)).toBe(false);
    expect(isWorthRetrying(0, REFUSED)).toBe(false);
  });
});

describe("useClickKeys", () => {
  it("keeps the keys of a click that had no certain answer, and no others", () => {
    const { result } = renderHook(() => useClickKeys());
    const first = result.current.take();
    expect(first.order).not.toBe(first.payment);
    expect(result.current.take()).toEqual(first);

    result.current.settle(OFFLINE);
    expect(result.current.take()).toEqual(first);

    result.current.settle(ENDED);
    const second = result.current.take();
    expect(second).not.toEqual(first);

    result.current.settle(null);
    expect(result.current.take()).not.toEqual(second);
  });
});

describe("the two calls", () => {
  const sent: Request[] = [];
  let answers: Response[] = [];

  beforeEach(() => {
    sent.length = 0;
    vi.stubGlobal("fetch", (request: Request) => {
      sent.push(request);
      return Promise.resolve(answers.shift()!);
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  const json = (status: number, body: object) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });

  it("orders the hold under the click's key", async () => {
    answers = [json(201, { id: "o-1" })];
    expect(await orderFor({ id: "r-1", order_id: null }, "k-1")).toBe("o-1");
    expect(sent[0]!.headers.get("Idempotency-Key")).toBe("k-1");
    expect(await sent[0]!.json()).toEqual({ reservation_id: "r-1" });
  });

  it("does not order a hold that has an order", async () => {
    expect(await orderFor({ id: "r-1", order_id: "o-9" }, "k")).toBe("o-9");
    expect(sent).toHaveLength(0);
  });

  it("goes on with the order another tab made", async () => {
    answers = [
      json(409, {
        type: "urn:ticketloop:problem:order-exists",
        title: "This reservation already has an order",
        status: 409,
        order_id: "o-2",
      }),
    ];
    expect(await orderFor({ id: "r-1", order_id: null }, "k")).toBe("o-2");
  });

  it("passes other refusals on", async () => {
    answers = [
      json(409, {
        type: "urn:ticketloop:problem:reservation-not-payable",
        title: "x",
        status: 409,
      }),
    ];
    await expect(orderFor({ id: "r-1", order_id: null }, "k")).rejects.toBeInstanceOf(ApiError);
  });

  it("says where to pay", async () => {
    answers = [json(201, { checkout_url: "http://pay/1" })];
    expect(await checkoutUrl("o-1", "k-2")).toBe("http://pay/1");
    expect(sent[0]!.headers.get("Idempotency-Key")).toBe("k-2");
    expect(await sent[0]!.json()).toEqual({ order_id: "o-1" });
  });
});

describe("checkoutProblem", () => {
  it("says what happened to the ticket and the money", () => {
    expect(checkoutProblem(GATEWAY_DOWN)).toMatch(/still held/);
    expect(checkoutProblem(REFUSED)).toMatch(/Nothing was charged/);
    expect(
      checkoutProblem(problem(409, "order-not-payable", { current_status: "CANCELLED" })),
    ).toMatch(/can't be paid for any more/);
    expect(checkoutProblem(ENDED)).toMatch(/has ended/);
    expect(checkoutProblem(OFFLINE)).toBeNull();
    expect(checkoutProblem(problem(403, "buyer-only"))).toBeNull();
  });
});
