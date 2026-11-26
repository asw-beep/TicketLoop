import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import type { Reservation } from "../api/types";
import { ReservationBadge } from "../components/Badges";
import { PayButton } from "../components/Checkout";
import { FormError } from "../components/Form";
import { Ticket as TicketCard } from "../components/Ticket";
import { formatPaise, formatWhen } from "../lib/format";
import { formatCountdown, holdState } from "../lib/reservations";
import { useServerClock } from "../lib/useServerClock";
import { isUuid } from "../lib/uuid";
import { NotFoundPage } from "./StatusPages";

/** One reservation's cache entry. The list is under "reservations". */
export const reservationKey = (reservationId: string) => ["reservation", reservationId] as const;

/** While a hold is live, ask again now and then: another tab can pay for it or cancel it. */
const REFETCH_MS = 30_000;

export function ReservationPage() {
  const { reservationId = "" } = useParams();
  const isReservationId = isUuid(reservationId);
  const reservation = useQuery({
    queryKey: reservationKey(reservationId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/reservations/{reservation_id}", {
          params: { path: { reservation_id: reservationId } },
        }),
      ),
    enabled: isReservationId,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "CREATED" || status === "PAYMENT_PENDING" ? REFETCH_MS : false;
    },
  });

  if (!isReservationId) return <NotFoundPage />;
  if (
    reservation.error instanceof ApiError &&
    [404, 422].includes(reservation.error.problem.status)
  ) {
    return <NotFoundPage />;
  }
  if (!reservation.data) return <FormError error={reservation.error} />;
  return <ReservationDetails reservation={reservation.data} />;
}

function ReservationDetails({ reservation }: { reservation: Reservation }) {
  const { listing } = reservation;
  const now = useServerClock(reservation.status === "CREATED", 250);
  const hold = holdState(reservation, now);
  const [failed, setFailed] = useState<unknown>(null);

  return (
    <>
      <TicketCard
        stub={
          <>
            <h1>Your hold</h1>
            <p className="stub__serial" title="Reservation ID">
              No. {reservation.id.slice(0, 8)}
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
            <dt>Price</dt>
            <dd>{formatPaise(listing.asking_price_paise)}</dd>
          </div>
          <div>
            <dt>Face value</dt>
            <dd>{formatPaise(listing.original_price_paise)}</dd>
          </div>
        </dl>
      </TicketCard>

      <section className="panel" aria-labelledby="hold-heading">
        <h2 id="hold-heading">
          Status <ReservationBadge reservation={reservation} now={now} />
        </h2>
        {hold.kind === "held" ? (
          <>
            <p className="lede">This ticket is yours to buy for</p>
            {/* Not a live region: reading the time out every second would drown everything
                else. The sentence that replaces it when the hold ends is one. */}
            <p className="countdown" role="timer" aria-label="Time left">
              {formatCountdown(hold.leftMs)}
            </p>
            <p className="meta">Nobody else can buy it until the time is up.</p>
            <PayButton hold={reservation} onFailed={(error) => setFailed(error)}>
              Checkout
            </PayButton>
            <CancelHold reservation={reservation} />
          </>
        ) : (
          <HoldOver kind={hold.kind} reservation={reservation} failed={failed} />
        )}
      </section>
    </>
  );
}

function HoldOver({
  kind,
  reservation,
  failed,
}: {
  kind: "paying" | "bought" | "ran-out" | "cancelled" | "failed";
  reservation: Reservation;
  /** Why Checkout failed, if it did: by now the hold shows as paying, so this says it. */
  failed: unknown;
}) {
  switch (kind) {
    case "paying":
      return (
        <>
          <div className="message" role="status">
            <p>You've started paying. The ticket stays yours until we know how it went.</p>
            <OrderLink reservation={reservation} />
          </div>
          {reservation.order_id && (
            <PayButton orderId={reservation.order_id} earlier={failed} quiet>
              Go to the payment page
            </PayButton>
          )}
        </>
      );
    case "bought":
      return (
        <div className="message message--ok" role="status">
          <p>You bought this ticket.</p>
          <OrderLink reservation={reservation} />
        </div>
      );
    case "failed":
      return (
        <div className="message message--error" role="status">
          <p>The payment failed, so the hold ended. Nothing was charged.</p>
          <ListingLink listingId={reservation.listing_id} />
        </div>
      );
    case "cancelled":
      return (
        <div className="message" role="status">
          <p>You gave up this hold. Nothing was charged.</p>
          <ListingLink listingId={reservation.listing_id} />
        </div>
      );
    case "ran-out":
      return (
        <div className="message" role="status">
          <p>Your hold ran out. Nothing was charged.</p>
          {/* Until the API has closed the hold, the listing's page is still hidden. */}
          {reservation.status === "CREATED" ? (
            <p>
              <Link to="/">Find another ticket</Link>.
            </p>
          ) : (
            <ListingLink listingId={reservation.listing_id} />
          )}
        </div>
      );
  }
}

function OrderLink({ reservation }: { reservation: Reservation }) {
  if (!reservation.order_id) return null;
  return (
    <p>
      <Link to={`/orders/${reservation.order_id}`}>Go to your order</Link>
    </p>
  );
}

function ListingLink({ listingId }: { listingId: string }) {
  return (
    <p>
      <Link to={`/listings/${listingId}`}>See if it is still for sale</Link> or{" "}
      <Link to="/">find another ticket</Link>.
    </p>
  );
}

function CancelHold({ reservation }: { reservation: Reservation }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const cancel = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/reservations/{reservation_id}/cancel", {
          params: { path: { reservation_id: reservation.id } },
        }),
      ),
    // Whatever happened, show what the API has now: a refusal means the hold already moved on.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: reservationKey(reservation.id) }),
        queryClient.invalidateQueries({ queryKey: ["reservations"] }),
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
        queryClient.invalidateQueries({ queryKey: ["order"] }),
        queryClient.invalidateQueries({ queryKey: ["listing", reservation.listing_id] }),
        queryClient.invalidateQueries({ queryKey: ["search"] }),
      ]),
  });

  if (!confirming) {
    return (
      <div className="actions">
        <button type="button" className="button button--quiet" onClick={() => setConfirming(true)}>
          Cancel hold
        </button>
      </div>
    );
  }
  return (
    <div role="group" aria-label="Confirm cancelling the hold">
      <FormError error={cancel.error} />
      <p>Give up this ticket? Someone else may buy it.</p>
      <div className="actions">
        <button
          type="button"
          className="button"
          onClick={() => cancel.mutate()}
          disabled={cancel.isPending}
          aria-busy={cancel.isPending}
        >
          Yes, give it up
        </button>
        <button type="button" className="link-button" onClick={() => setConfirming(false)}>
          Keep it
        </button>
      </div>
    </div>
  );
}
