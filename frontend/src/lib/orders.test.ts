import { describe, expect, it } from "vitest";
import { pollEvery } from "./orders";

describe("pollEvery", () => {
  it("asks often at first, then less often", () => {
    expect(pollEvery("PAYMENT_PENDING", 0)).toBe(2_000);
    expect(pollEvery("PAYMENT_PENDING", 29)).toBe(2_000);
    expect(pollEvery("PAYMENT_PENDING", 30)).toBe(10_000);
  });

  it.each(["PAID", "TRANSFER_PENDING"] as const)(
    "keeps asking about a %s order until the ticket has been handed over",
    (status) => {
      expect(pollEvery(status, 0)).toBe(2_000);
      expect(pollEvery(status, 30)).toBe(10_000);
    },
  );

  it.each(["CREATED", "PAYMENT_FAILED", "CANCELLED", "COMPLETED", "TRANSFER_FAILED"] as const)(
    "does not ask again about a %s order",
    (status) => {
      expect(pollEvery(status, 0)).toBe(false);
    },
  );

  it("does not ask before the first answer", () => {
    expect(pollEvery(undefined, 0)).toBe(false);
  });
});
