/**
 * Paying for a held ticket: make the order, start the payment, and say where to go. Both calls
 * carry an Idempotency-Key, so a click whose answer was lost can be sent again and is still one
 * click: one order, one payment.
 */
import { useRef } from "react";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";

export interface Keys {
  order: string;
  payment: string;
}

export function newKey(): string {
  // Not crypto.randomUUID(): browsers offer that only over https and on localhost.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** True if we cannot know whether the request did anything: it must be sent again as it was. */
export function isUncertain(error: unknown): boolean {
  if (error instanceof TypeError) return true; // no answer at all
  if (!(error instanceof ApiError)) return false;
  if (error.code === "payment-refused") return false; // a 502, but a certain no
  return error.problem.status >= 500 || error.code === "idempotency-key-in-progress";
}

/** Worth sending again by itself, without asking the buyer to press anything. */
export function isWorthRetrying(failures: number, error: unknown): boolean {
  if (error instanceof ApiError && error.code === "payment-gateway-unavailable") return false;
  return failures < 3 && isUncertain(error);
}

/** One pair of keys per click, the same pair until that click has had a certain answer. */
export function useClickKeys(): { take: () => Keys; settle: (error: unknown) => void } {
  const keys = useRef<Keys | null>(null);
  return {
    take: () => (keys.current ??= { order: newKey(), payment: newKey() }),
    settle: (error) => {
      if (!isUncertain(error)) keys.current = null;
    },
  };
}

/** The order for this hold: the one it has, or a new one. */
export async function orderFor(
  hold: { id: string; order_id: string | null },
  key: string,
): Promise<string> {
  if (hold.order_id) return hold.order_id;
  try {
    const order = await unwrap(
      api.POST("/api/v1/orders", {
        params: { header: { "idempotency-key": key } },
        body: { reservation_id: hold.id },
      }),
    );
    return order.id;
  } catch (error) {
    // Ordered in another tab: that order is ours, so go on with it.
    const existing = orderIdIn(error);
    if (existing) return existing;
    throw error;
  }
}

function orderIdIn(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.code !== "order-exists") return null;
  const { order_id } = error.problem as { order_id?: unknown };
  return typeof order_id === "string" ? order_id : null;
}

/** Where to send the buyer to pay for the order. */
export async function checkoutUrl(orderId: string, key: string): Promise<string> {
  const intent = await unwrap(
    api.POST("/api/v1/payments/intents", {
      params: { header: { "idempotency-key": key } },
      body: { order_id: orderId },
    }),
  );
  return intent.checkout_url;
}

/** One sentence on why the buyer could not go on to pay. */
export function checkoutProblem(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  switch (error.code) {
    case "payment-gateway-unavailable":
      return "The payment service isn't answering. Your ticket is still held; try again in a moment.";
    case "payment-refused":
      return "The payment service refused this payment. Nothing was charged, and the hold has ended.";
    case "reservation-not-payable":
      return "This hold has ended, so it can't be paid for.";
    case "order-not-payable":
      return "This order can't be paid for any more. Its page says how it ended.";
    default:
      return null;
  }
}
