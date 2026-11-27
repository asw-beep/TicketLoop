import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, unwrap } from "../api/client";
import type { Reservation } from "../api/types";
import { ReservationBadge } from "../components/Badges";
import { Poster } from "../components/Poster";
import { FormError } from "../components/Form";
import { formatPaise, formatWhen } from "../lib/format";
import { holdState } from "../lib/reservations";
import { useServerClock } from "../lib/useServerClock";

/** Every hold of the signed-in buyer. Every change to a hold invalidates it. */
export const MY_HOLDS_KEY = ["reservations", "me"] as const;

/** While any hold is live, ask again now and then. */
const REFETCH_MS = 30_000;

export function MyHoldsPage() {
  const holds = useInfiniteQuery({
    queryKey: MY_HOLDS_KEY,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/reservations/me", {
          params: { query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.items.some((hold) => hold.status === "CREATED"))
        ? REFETCH_MS
        : false,
  });
  const items = holds.data?.pages.flatMap((page) => page.items) ?? [];
  // A hold can run out while this page is open.
  const now = useServerClock(
    items.some((hold) => hold.status === "CREATED"),
    1000,
  );

  return (
    <>
      <div className="page-head">
        <h1>My holds</h1>
      </div>
      <p className="lede">
        Tickets you've reserved. A hold lasts 10 minutes; the ticket then goes back on sale unless
        you've started paying.
      </p>

      <FormError error={holds.error} />
      {holds.isSuccess && items.length === 0 && (
        <p className="empty">
          You haven't reserved any tickets. <Link to="/">Find one</Link>.
        </p>
      )}
      <ul className="list" aria-label="Your holds">
        {items.map((reservation) => (
          <HoldRow key={reservation.id} reservation={reservation} now={now} />
        ))}
      </ul>
      {holds.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => holds.fetchNextPage()}
            disabled={holds.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

function HoldRow({ reservation, now }: { reservation: Reservation; now: number }) {
  const { listing } = reservation;
  const held = holdState(reservation, now).kind === "held";
  return (
    <li className="card card--thumb" aria-label={listing.event.name}>
      <Poster event={listing.event} size="thumb" />
      <div className="card__main">
        <div className="card__top">
          <Link className="card__title" to={`/reservations/${reservation.id}`}>
            {listing.event.name}
          </Link>
          <ReservationBadge reservation={reservation} now={now} />
        </div>
        <p className="meta">
          {formatWhen(listing.event.starts_at)} · {formatPaise(listing.asking_price_paise)} ·{" "}
          {held
            ? `held until ${formatWhen(reservation.expires_at)}`
            : `reserved ${formatWhen(reservation.created_at)}`}
        </p>
      </div>
    </li>
  );
}
