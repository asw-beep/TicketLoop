import { Link } from "react-router";
import type { PublicListing } from "../api/types";
import { formatPaise, formatWhen } from "../lib/format";
import { withoutRepeats } from "../lib/search";
import { FormError } from "./Form";

/** Enough of an infinite query to show its pages. */
interface Pages {
  data?: { pages: { items: PublicListing[] }[] };
  error: unknown;
  isSuccess: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}

/** Tickets on sale, with Load more. `showEvent` is off on an event's own page. */
export function ListingResults({
  results,
  empty,
  showEvent = true,
}: {
  results: Pages;
  empty: string;
  showEvent?: boolean;
}) {
  const items = withoutRepeats(results.data?.pages.flatMap((page) => page.items) ?? []);
  const none = results.isSuccess && items.length === 0;
  return (
    <>
      <FormError error={results.error} />
      {/* Always here, so a screen reader hears what a search found. */}
      <p role="status" className={none ? "empty" : "visually-hidden"}>
        {none ? empty : results.isSuccess && `Showing ${items.length} tickets.`}
      </p>
      <ul className="list" aria-label="Tickets on sale">
        {items.map((listing) => (
          <ResultRow key={listing.id} listing={listing} showEvent={showEvent} />
        ))}
      </ul>
      {results.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => results.fetchNextPage()}
            disabled={results.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

function ResultRow({ listing, showEvent }: { listing: PublicListing; showEvent: boolean }) {
  const { event } = listing;
  const discounted = listing.asking_price_paise < listing.original_price_paise;
  const seat = [listing.ticket_category, listing.seat_details].filter(Boolean).join(" · ");
  return (
    <li className="card" aria-label={showEvent ? event.name : seat}>
      <div className="card__top">
        <Link className="card__title" to={`/listings/${listing.id}`}>
          {showEvent ? event.name : seat}
        </Link>
        <span className="price">
          {discounted && (
            <s className="price__was">
              <span className="visually-hidden">Face value </span>
              {formatPaise(listing.original_price_paise)}
            </s>
          )}
          <strong>{formatPaise(listing.asking_price_paise)}</strong>
        </span>
      </div>
      {showEvent && (
        <>
          <p className="meta">{seat}</p>
          <p className="meta">
            {event.venue}, {event.city} · {formatWhen(event.starts_at)} ·{" "}
            <Link to={`/events/${event.id}`}>All tickets for this event</Link>
          </p>
        </>
      )}
    </li>
  );
}
