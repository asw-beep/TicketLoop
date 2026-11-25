import { describe, expect, it } from "vitest";
import { ApiError } from "../api/problem";
import type { Reservation } from "../api/types";
import { formatCountdown, holdState, reserveProblem } from "./reservations";

const END = Date.parse("2026-11-26T12:10:00Z");

function reservation(status: Reservation["status"]): Pick<Reservation, "status" | "expires_at"> {
  return { status, expires_at: "2026-11-26T12:10:00Z" };
}

describe("holdState", () => {
  it("is held until the moment it ends", () => {
    expect(holdState(reservation("CREATED"), END - 1)).toEqual({ kind: "held", leftMs: 1 });
    // The API may not have closed it yet: its job runs every 30 seconds.
    expect(holdState(reservation("CREATED"), END)).toEqual({ kind: "ran-out" });
  });

  it("is being paid for even after the clock ran out", () => {
    expect(holdState(reservation("PAYMENT_PENDING"), END + 60_000)).toEqual({ kind: "paying" });
  });

  it.each([
    ["EXPIRED", "ran-out"],
    ["CANCELLED", "cancelled"],
    ["FAILED", "failed"],
    ["CONFIRMED", "bought"],
  ] as const)("%s is %s", (status, kind) => {
    expect(holdState(reservation(status), END - 60_000)).toEqual({ kind });
  });
});

describe("formatCountdown", () => {
  it.each([
    [600_000, "10:00"],
    [599_001, "10:00"], // the second that has started still counts
    [599_000, "9:59"],
    [61_000, "1:01"],
    [1, "0:01"],
    [0, "0:00"],
    [-5_000, "0:00"],
  ])("%d ms is %s", (ms, text) => {
    expect(formatCountdown(ms)).toBe(text);
  });
});

function apiError(status: number, code: string, extra: Record<string, unknown> = {}): ApiError {
  return new ApiError({
    type: `urn:ticketloop:problem:${code}`,
    title: "A title",
    detail: "The API's own words.",
    status,
    ...extra,
  });
}

describe("reserveProblem", () => {
  it.each([
    ["reserved", /Someone else reserved it/],
    ["sold", /sold/],
    ["expired", /no longer for sale/],
    ["hold-ended", /Your hold ran out/],
  ])("explains a listing that is %s", (reason, text) => {
    expect(reserveProblem(apiError(409, "listing-not-available", { reason }))).toMatch(text);
  });

  it("says a listing that has gone is no longer for sale", () => {
    expect(reserveProblem(apiError(404, "listing-not-found"))).toBe(
      "This ticket is no longer for sale.",
    );
  });

  it("uses the API's words for anything else", () => {
    expect(reserveProblem(apiError(409, "listing-not-available", { reason: "new" }))).toBe(
      "The API's own words.",
    );
    expect(reserveProblem(apiError(403, "own-listing-reservation"))).toBe("The API's own words.");
  });
});
