import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, unwrap } from "../api/client";
import type { Order } from "../api/types";
import { OrderBadge } from "../components/Badges";
import { FormError } from "../components/Form";
import { formatPaise, formatWhen } from "../lib/format";
import { MY_ORDERS_KEY } from "../lib/orders";

/** While any payment is undecided, ask again now and then. */
const REFETCH_MS = 10_000;

export function MyOrdersPage() {
  const orders = useInfiniteQuery({
    queryKey: MY_ORDERS_KEY,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/orders/me", {
          params: { query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((order) => order.status === "PAYMENT_PENDING"),
      )
        ? REFETCH_MS
        : false,
  });
  const items = orders.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>My orders</h1>
      </div>
      <p className="lede">Tickets you've bought, or started to buy, newest first.</p>

      <FormError error={orders.error} />
      {orders.isSuccess && items.length === 0 && (
        <p className="empty">
          You haven't ordered any tickets. <Link to="/">Find one</Link>.
        </p>
      )}
      <ul className="list" aria-label="Your orders">
        {items.map((order) => (
          <OrderRow key={order.id} order={order} />
        ))}
      </ul>
      {orders.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => orders.fetchNextPage()}
            disabled={orders.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

function OrderRow({ order }: { order: Order }) {
  const { listing } = order;
  return (
    <li className="card" aria-label={listing.event.name}>
      <div className="card__top">
        <Link className="card__title" to={`/orders/${order.id}`}>
          {listing.event.name}
        </Link>
        <OrderBadge status={order.status} />
      </div>
      <p className="meta">
        {formatWhen(listing.event.starts_at)} · {formatPaise(order.amount_paise)} · ordered{" "}
        {formatWhen(order.created_at)}
      </p>
    </li>
  );
}
