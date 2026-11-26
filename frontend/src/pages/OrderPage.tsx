import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import type { Order } from "../api/types";
import { OrderBadge } from "../components/Badges";
import { PayButton } from "../components/Checkout";
import { FormError } from "../components/Form";
import { Ticket as TicketCard } from "../components/Ticket";
import { formatPaise, formatWhen } from "../lib/format";
import { orderKey, pollEvery } from "../lib/orders";
import { isUuid } from "../lib/uuid";
import { NotFoundPage } from "./StatusPages";

export function OrderPage() {
  const { orderId = "" } = useParams();
  const isOrderId = isUuid(orderId);
  const order = useQuery({
    queryKey: orderKey(orderId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/orders/{order_id}", { params: { path: { order_id: orderId } } })),
    enabled: isOrderId,
    retry: false,
    // Coming back from the gateway, the page is here before the gateway's webhook is.
    refetchInterval: (query) => pollEvery(query.state.data?.status, query.state.dataUpdateCount),
  });

  if (!isOrderId) return <NotFoundPage />;
  if (order.error instanceof ApiError && [404, 422].includes(order.error.problem.status)) {
    return <NotFoundPage />;
  }
  if (!order.data) return <FormError error={order.error} />;
  return <OrderDetails order={order.data} />;
}

function OrderDetails({ order }: { order: Order }) {
  const { listing } = order;
  return (
    <>
      <TicketCard
        stub={
          <>
            <h1>Your order</h1>
            <p className="stub__serial" title="Order ID">
              No. {order.id.slice(0, 8)}
            </p>
          </>
        }
      >
        <p className="pass__name">{listing.event.name}</p>
        <p className="pass__email">
          {listing.event.venue}, {listing.event.city} · {formatWhen(listing.event.starts_at)}
        </p>
        <dl className="details">
          <div>
            <dt>Category</dt>
            <dd>{listing.ticket_category}</dd>
          </div>
          <div>
            <dt>Seat</dt>
            <dd>{listing.seat_details ?? "Not given"}</dd>
          </div>
          <div>
            <dt>Amount</dt>
            <dd>{formatPaise(order.amount_paise)}</dd>
          </div>
          <div>
            <dt>Ordered</dt>
            <dd>{formatWhen(order.created_at)}</dd>
          </div>
        </dl>
      </TicketCard>

      <section className="panel" aria-labelledby="order-heading">
        <h2 id="order-heading">
          Status <OrderBadge status={order.status} />
        </h2>
        <OrderStatusText order={order} />
      </section>
    </>
  );
}

function OrderStatusText({ order }: { order: Order }) {
  switch (order.status) {
    case "CREATED":
      return (
        <p className="lede">
          Not paid yet. <Link to={`/reservations/${order.reservation_id}`}>Go to your hold</Link> to
          pay before it runs out.
        </p>
      );
    case "PAYMENT_PENDING":
      return (
        <>
          <p className="lede" role="status">
            Waiting for the payment service to tell us. This page updates by itself.
          </p>
          <p className="meta">
            If you haven't paid, you still can. The ticket stays yours until we know.
          </p>
          <PayButton orderId={order.id} quiet>
            Go to the payment page
          </PayButton>
        </>
      );
    case "PAID":
    case "TRANSFER_PENDING":
      return (
        <div className="message message--ok" role="status">
          <p>
            You've paid {formatPaise(order.amount_paise)}. We're handing the ticket over to you.
            This page updates by itself.
          </p>
        </div>
      );
    case "COMPLETED":
      return (
        <div className="message message--ok" role="status">
          <p>You've paid {formatPaise(order.amount_paise)}. The ticket is yours.</p>
          <p>
            <Link to="/tickets">See it with your tickets</Link>.
          </p>
        </div>
      );
    case "TRANSFER_FAILED":
      return (
        <div className="message message--error" role="status">
          <p>You've paid, but the ticket couldn't be transferred. We're looking into it.</p>
        </div>
      );
    case "PAYMENT_FAILED":
      return (
        <div className="message message--error" role="status">
          <p>The payment failed. Nothing was charged, and your hold has ended.</p>
          <TryAgain order={order} />
        </div>
      );
    case "CANCELLED":
      return (
        <div className="message" role="status">
          <p>This order was cancelled. Nothing was charged.</p>
          <TryAgain order={order} />
        </div>
      );
  }
}

function TryAgain({ order }: { order: Order }) {
  return (
    <p>
      <Link to={`/listings/${order.listing_id}`}>See if it is still for sale</Link> or{" "}
      <Link to="/">find another ticket</Link>.
    </p>
  );
}
