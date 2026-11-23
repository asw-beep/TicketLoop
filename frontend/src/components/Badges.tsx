import type { ListingStatus, VerificationStatus } from "../api/types";

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

export function ListingBadge({ status }: { status: ListingStatus }) {
  return <span className={LISTING_CLASS[status]}>{LISTING_NAMES[status]}</span>;
}
