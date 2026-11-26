/**
 * Listing rules the screens need: which shape the API sent, what the seller may do in each
 * status, and how a history entry reads. The API enforces all of it; this only decides what
 * to show.
 */
import type { HistoryEntry, Listing, ListingStatus, PublicListing } from "../api/types";
import { formatPaise, formatWhen, rupeesToPaise } from "./format";

/** The lowest asking price the API accepts: ₹1. */
export const MIN_PRICE_PAISE = 100;

/**
 * A typed asking price, checked the way the API will check it: from ₹1 to what the seller
 * paid. Paise to send, or a sentence saying what to fix.
 */
export function checkAskingPrice(
  input: string,
  originalPaise: number,
): { paise: number } | { error: string } {
  const paise = rupeesToPaise(input);
  if (paise === null) return { error: "Enter a price in rupees, like 2,000 or 1999.50." };
  if (paise < MIN_PRICE_PAISE) return { error: "The price must be at least ₹1." };
  if (paise > originalPaise) {
    return { error: `The price can be at most ${formatPaise(originalPaise)}, what you paid.` };
  }
  return { paise };
}

/** A listing in one of these still has a claim on its ticket: the ticket can't be listed again. */
export const OPEN_STATUSES: ListingStatus[] = [
  "DRAFT",
  "PENDING_VERIFICATION",
  "ACTIVE",
  "RESERVED",
];

/** GET /listings/{id} sends the full shape to the seller and admins; only it has ticket_id. */
export function isFullListing(listing: Listing | PublicListing): listing is Listing {
  return "ticket_id" in listing;
}

export type SellerAction = "submit" | "edit" | "cancel";

/** What the seller can do now. Reserved and closed listings can't be changed. */
export function sellerActions(status: ListingStatus): SellerAction[] {
  if (status === "DRAFT") return ["submit", "edit", "cancel"];
  if (status === "PENDING_VERIFICATION" || status === "ACTIVE") return ["edit", "cancel"];
  return [];
}

function money(value: unknown): string {
  return typeof value === "number" ? formatPaise(value) : "?";
}

const RELEASED: Record<string, string> = {
  buyer_cancelled: "Hold given up",
  hold_expired: "Hold ran out; back on sale",
  payment_failed: "Payment failed; back on sale",
  payment_timeout: "Payment never made; back on sale",
};

/** One line of a listing's history, in words. */
export function historyLabel(entry: HistoryEntry): string {
  const { details } = entry;
  switch (entry.action) {
    case "listing.created":
      return "Listed as a draft";
    case "listing.submitted":
      return "Submitted for review";
    case "listing.approved":
      return "Approved";
    case "listing.rejected":
      return typeof details.reason === "string" ? `Rejected: ${details.reason}` : "Rejected";
    case "listing.price_changed":
      return `Price changed from ${money(details.old_price_paise)} to ${money(details.new_price_paise)}`;
    case "listing.expiry_changed":
      return typeof details.new_expires_at === "string"
        ? `Listing end changed to ${formatWhen(details.new_expires_at)}`
        : "Listing end changed";
    case "listing.cancelled":
      return details.reason === "event_cancelled"
        ? "Cancelled because the event was cancelled"
        : "Cancelled";
    case "listing.expired":
      return details.reason === "buyer_cancelled"
        ? "Hold given up past the listing's end"
        : "Expired";
    case "listing.reserved":
      return "Reserved";
    case "listing.sold":
      return "Sold; the ticket went to the buyer";
    case "listing.released":
      return RELEASED[String(details.reason)] ?? "Back on sale";
    default:
      return entry.action;
  }
}
