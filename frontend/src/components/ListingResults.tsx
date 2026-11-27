import { Link } from "react-router";
import type { PublicListing } from "../api/types";
import { formatPaise, formatWhen } from "../lib/format";
import { withoutRepeats } from "../lib/search";
import { FormError } from "./Form";
import { Poster } from "./Poster";

/** Enough of an infinite query to show its pages. */
interface Pages {
  data?: { pages: { items: PublicListing[] }[] };
  error: unknown;
  isSuccess: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}

/**
 * Tickets on sale, with Load more. Across events they are a grid of poster cards; on an event's
 * own page (`showEvent` off) they are compact rows, since every row would carry the same poster.
 */
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
      <ul className={showEvent ? "poster-grid" : "list"} aria-label="Tickets on sale">
        {items.map((listing, index) =>
          showEvent ? (
            <PosterCard key={listing.id} listing={listing} index={index} />
          ) : (
            <ResultRow key={listing.id} listing={listing} />
          ),
        )}
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

function Price({ listing }: { listing: PublicListing }) {
  const discounted = listing.asking_price_paise < listing.original_price_paise;
  return (
    <span className="price">
      {discounted && (
        <s className="price__was">
          <span className="visually-hidden">Face value </span>
          {formatPaise(listing.original_price_paise)}
        </s>
      )}
      <strong>{formatPaise(listing.asking_price_paise)}</strong>
    </span>
  );
}

const seatOf = (listing: PublicListing) =>
  [listing.ticket_category, listing.seat_details].filter(Boolean).join(" · ");

/** A ticket on sale across events: the event's poster, then what and where, then the price. */
function PosterCard({ listing, index }: { listing: PublicListing; index: number }) {
  const { event } = listing;
  return (
    <li className="poster-card" aria-label={event.name}>
      <Poster event={event} size="card" index={index} />
      <Link className="card__title" to={`/listings/${listing.id}`}>
        {event.name}
      </Link>
      <p className="meta">{seatOf(listing)}</p>
      <p className="meta">
        {event.venue}, {event.city} · {formatWhen(event.starts_at)} ·{" "}
        <Link to={`/events/${event.id}`}>All tickets for this event</Link>
      </p>
      <Price listing={listing} />
    </li>
  );
}

/** A ticket on sale on its event's own page: the seat and the price. */
function ResultRow({ listing }: { listing: PublicListing }) {
  const seat = seatOf(listing);
  return (
    <li className="card" aria-label={seat}>
      <div className="card__top">
        <Link className="card__title" to={`/listings/${listing.id}`}>
          {seat}
        </Link>
        <Price listing={listing} />
      </div>
    </li>
  );
}
