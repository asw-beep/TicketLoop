import type { VerificationStatus } from "../api/types";

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
