/**
 * Reservation rules the screens need: what a hold is now, how long is left, and why a ticket
 * could not be reserved. The API decides all of it; this only decides what to say.
 */
import { ApiError, describeError } from "../api/problem";
import type { Reservation } from "../api/types";

export type HoldState =
  | { kind: "held"; leftMs: number }
  | { kind: "paying" | "bought" | "ran-out" | "cancelled" | "failed" };

/**
 * A CREATED hold past its end is over although the API may not have closed it yet: nobody can
 * pay for it any more, so saying "held" would be a promise it can't keep.
 */
export function holdState(
  { status, expires_at }: Pick<Reservation, "status" | "expires_at">,
  now: number,
): HoldState {
  switch (status) {
    case "CREATED": {
      const leftMs = Date.parse(expires_at) - now;
      return leftMs > 0 ? { kind: "held", leftMs } : { kind: "ran-out" };
    }
    case "PAYMENT_PENDING":
      return { kind: "paying" };
    case "CONFIRMED":
      return { kind: "bought" };
    case "EXPIRED":
      return { kind: "ran-out" };
    case "CANCELLED":
      return { kind: "cancelled" };
    case "FAILED":
      return { kind: "failed" };
  }
}

/** 9 minutes 5 seconds as "9:05". Rounds up, so it reaches 0:00 only when the time is gone. */
export function formatCountdown(ms: number): string {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

const NOT_AVAILABLE: Record<string, string> = {
  reserved:
    "Someone else reserved it first. If they don't buy it, it may come back within 10 minutes.",
  sold: "It has been sold.",
  expired: "This ticket is no longer for sale.",
  "hold-ended": "Your hold ran out. Try again in a minute, if it comes back on sale.",
};

/** One sentence on why a ticket couldn't be reserved. */
export function reserveProblem(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.problem.status === 404) return NOT_AVAILABLE.expired!;
    const { reason } = error.problem as { reason?: unknown };
    if (error.code === "listing-not-available" && typeof reason === "string") {
      return NOT_AVAILABLE[reason] ?? describeError(error);
    }
  }
  return describeError(error);
}
