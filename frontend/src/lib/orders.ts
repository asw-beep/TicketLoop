/** What an order's status means to its buyer. */
import type { OrderStatus } from "../api/types";

export const ORDER_NAMES: Record<OrderStatus, string> = {
  CREATED: "Not paid yet",
  PAYMENT_PENDING: "Payment pending",
  PAID: "Paid",
  PAYMENT_FAILED: "Payment failed",
  CANCELLED: "Cancelled",
  TRANSFER_PENDING: "Transferring",
  COMPLETED: "Completed",
  TRANSFER_FAILED: "Transfer failed",
};

/** Every order of the signed-in buyer. Every change to an order invalidates it. */
export const MY_ORDERS_KEY = ["orders", "me"] as const;

/** One order's cache entry. The list is under "orders". */
export const orderKey = (orderId: string) => ["order", orderId] as const;

/**
 * How long until asking again, while the gateway decides: every 2 seconds for the first minute,
 * every 10 after that, and not at all once there is nothing to wait for.
 */
export function pollEvery(status: OrderStatus | undefined, asked: number): number | false {
  if (status !== "PAYMENT_PENDING") return false;
  return asked < 30 ? 2_000 : 10_000;
}
