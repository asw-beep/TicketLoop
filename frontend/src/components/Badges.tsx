import type { ListingStatus, OrderStatus, Reservation, VerificationStatus } from "../api/types";
import { ORDER_NAMES } from "../lib/orders";
import { holdState, type HoldState } from "../lib/reservations";

export const VERIFICATION_NAMES: Record<VerificationStatus, string> = {
  PENDING: "Pending review",
  VERIFIED: "Verified",
  REJECTED: "Rejected",
};

const CLASS: Record<VerificationStatus, string> = {
  PENDING: "badge",
  VERIFIED: "badge badge--ok",
  REJECTED: "badge badge--bad",
};

export function VerificationBadge({ status }: { status: VerificationStatus }) {
  return <span className={CLASS[status]}>{VERIFICATION_NAMES[status]}</span>;
}

export const LISTING_NAMES: Record<ListingStatus, string> = {
  DRAFT: "Draft",
  PENDING_VERIFICATION: "Pending review",
  ACTIVE: "Active",
  RESERVED: "Reserved",
  SOLD: "Sold",
  REJECTED: "Rejected",
  CANCELLED: "Cancelled",
  EXPIRED: "Expired",
};

const LISTING_CLASS: Record<ListingStatus, string> = {
  DRAFT: "badge",
  PENDING_VERIFICATION: "badge",
  ACTIVE: "badge badge--live",
  RESERVED: "badge badge--live",
  SOLD: "badge badge--ok",
  REJECTED: "badge badge--bad",
  CANCELLED: "badge",
  EXPIRED: "badge",
};

const HOLD_NAMES: Record<HoldState["kind"], string> = {
  held: "On hold",
  paying: "Payment pending",
  bought: "Bought",
  "ran-out": "Ran out",
  cancelled: "Cancelled",
  failed: "Payment failed",
};

const HOLD_CLASS: Record<HoldState["kind"], string> = {
  held: "badge badge--live",
  paying: "badge badge--live",
  bought: "badge badge--ok",
  "ran-out": "badge",
  cancelled: "badge",
  failed: "badge badge--bad",
};

export function ReservationBadge({ reservation, now }: { reservation: Reservation; now: number }) {
  const { kind } = holdState(reservation, now);
  return <span className={HOLD_CLASS[kind]}>{HOLD_NAMES[kind]}</span>;
}

const ORDER_CLASS: Record<OrderStatus, string> = {
  CREATED: "badge badge--live",
  PAYMENT_PENDING: "badge badge--live",
  PAID: "badge badge--ok",
  PAYMENT_FAILED: "badge badge--bad",
  CANCELLED: "badge",
  TRANSFER_PENDING: "badge badge--live",
  COMPLETED: "badge badge--ok",
  TRANSFER_FAILED: "badge badge--bad",
};

export function OrderBadge({ status }: { status: OrderStatus }) {
  return <span className={ORDER_CLASS[status]}>{ORDER_NAMES[status]}</span>;
}

export function ListingBadge({ status }: { status: ListingStatus }) {
  return <span className={LISTING_CLASS[status]}>{LISTING_NAMES[status]}</span>;
}
