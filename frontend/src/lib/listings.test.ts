import { describe, expect, it } from "vitest";
import type { HistoryEntry, Listing, ListingStatus, PublicListing } from "../api/types";
import { checkAskingPrice, historyLabel, isFullListing, sellerActions } from "./listings";

describe("sellerActions", () => {
  it.each<[ListingStatus, string[]]>([
    ["DRAFT", ["submit", "edit", "cancel"]],
    ["PENDING_VERIFICATION", ["edit", "cancel"]],
    ["ACTIVE", ["edit", "cancel"]],
    ["RESERVED", []],
    ["SOLD", []],
    ["REJECTED", []],
    ["CANCELLED", []],
    ["EXPIRED", []],
  ])("%s allows %j", (status, actions) => {
    expect(sellerActions(status)).toEqual(actions);
  });
});

const PUBLIC: PublicListing = {
  id: "5f0c0000-0000-4000-8000-000000000001",
  event: {
    id: "5f0c0000-0000-4000-8000-000000000002",
    name: "Monsoon Jazz",
    category: "CONCERT",
    city: "Kochi",
    venue: "Bolgatty Palace",
    starts_at: "2027-01-15T13:00:00Z",
  },
  ticket_category: "GENERAL",
  seat_details: null,
  asking_price_paise: 200_000,
  original_price_paise: 250_000,
  status: "ACTIVE",
  created_at: "2027-01-01T10:00:00Z",
};

describe("isFullListing", () => {
  it("is true for the seller's and admins' shape, which has ticket_id", () => {
    const full: Listing = {
      ...PUBLIC,
      ticket_id: "5f0c0000-0000-4000-8000-000000000003",
      seller_id: "5f0c0000-0000-4000-8000-000000000004",
      expires_at: "2027-01-15T13:00:00Z",
      reviewed_at: null,
      rejection_reason: null,
      updated_at: "2027-01-01T10:00:00Z",
    };
    expect(isFullListing(full)).toBe(true);
  });

  it("is false for the public shape", () => {
    expect(isFullListing(PUBLIC)).toBe(false);
  });
});

describe("historyLabel for a reserved listing", () => {
  it.each([
    ["listing.reserved", {}, "Reserved"],
    ["listing.released", { reason: "hold_expired" }, "Hold ran out; back on sale"],
    ["listing.released", { reason: "buyer_cancelled" }, "Hold given up"],
    ["listing.expired", { reason: "buyer_cancelled" }, "Hold given up past the listing's end"],
    ["listing.expired", {}, "Expired"],
  ])("%s %j", (action, details, label) => {
    expect(historyLabel(entry(action, details))).toBe(label);
  });
});

function entry(action: string, details: Record<string, unknown> = {}): HistoryEntry {
  return {
    action,
    details,
    from_status: null,
    to_status: null,
    actor_role: "seller",
    created_at: "2027-01-01T10:00:00Z",
  };
}

describe("historyLabel", () => {
  it.each<[HistoryEntry, string]>([
    [entry("listing.created"), "Listed as a draft"],
    [entry("listing.submitted"), "Submitted for review"],
    [entry("listing.approved"), "Approved"],
    [entry("listing.rejected", { reason: "Seat number missing" }), "Rejected: Seat number missing"],
    [
      entry("listing.price_changed", { old_price_paise: 250_000, new_price_paise: 200_000 }),
      "Price changed from ₹2,500 to ₹2,000",
    ],
    [
      entry("listing.cancelled", { reason: "event_cancelled" }),
      "Cancelled because the event was cancelled",
    ],
    [entry("listing.cancelled"), "Cancelled"],
    [entry("listing.expired"), "Expired"],
  ])("labels %j", (historyEntry, label) => {
    expect(historyLabel(historyEntry)).toBe(label);
  });
});

describe("checkAskingPrice", () => {
  it.each<[string, number]>([
    ["2,000", 200_000],
    ["2500", 250_000], // equal to what was paid is fine
    ["1", 100],
    [" 1999.50 ", 199_950],
  ])("accepts %s as %i paise", (input, paise) => {
    expect(checkAskingPrice(input, 250_000)).toEqual({ paise });
  });

  it.each<[string, RegExp]>([
    ["2500.01", /at most ₹2,500/i],
    ["0.99", /at least ₹1/i],
    ["abc", /rupees/i],
    ["", /rupees/i],
  ])("refuses %j", (input, message) => {
    const result = checkAskingPrice(input, 250_000);
    expect("error" in result && result.error).toMatch(message);
  });
});
