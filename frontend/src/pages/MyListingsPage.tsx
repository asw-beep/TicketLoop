import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, unwrap } from "../api/client";
import type { Listing, ListingStatus } from "../api/types";
import { LISTING_NAMES, ListingBadge } from "../components/Badges";
import { Poster } from "../components/Poster";
import { FormError, SelectField } from "../components/Form";
import { formatPaise, formatWhen } from "../lib/format";

/** Everything the signed-in seller has listed. Every listing change invalidates it. */
export const MY_LISTINGS_KEY = ["listings", "me"] as const;

/** Every listing status, in the order the Status filters offer them. */
export const STATUSES = Object.keys(LISTING_NAMES) as ListingStatus[];

export function MyListingsPage() {
  const [status, setStatus] = useState<ListingStatus | "">("");
  const listings = useInfiniteQuery({
    queryKey: [...MY_LISTINGS_KEY, status],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/listings/me", {
          params: {
            query: {
              limit: 50,
              ...(status ? { status } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const items = listings.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>My listings</h1>
      </div>
      <p className="lede">
        Tickets you've offered for resale. List a verified ticket from its page; an admin reviews
        each listing before buyers can see it.
      </p>

      <SelectField
        label="Status"
        name="status"
        value={status}
        onChange={(event) => setStatus(event.target.value as ListingStatus | "")}
      >
        <option value="">All</option>
        {STATUSES.map((value) => (
          <option key={value} value={value}>
            {LISTING_NAMES[value]}
          </option>
        ))}
      </SelectField>

      <FormError error={listings.error} />
      {listings.isSuccess && items.length === 0 && (
        <p className="empty">
          {status ? "No listings with this status." : "You haven't listed any tickets yet."}
        </p>
      )}
      <ul className="list" aria-label="Your listings">
        {items.map((listing) => (
          <ListingRow key={listing.id} listing={listing} thumb />
        ))}
      </ul>
      {listings.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => listings.fetchNextPage()}
            disabled={listings.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

/**
 * One listing in a list: the event, its status and price, linking to the listing's page. With
 * `thumb` (the seller's own list) it carries the event's poster; admin screens go without.
 */
export function ListingRow({ listing, thumb = false }: { listing: Listing; thumb?: boolean }) {
  return (
    <li className={thumb ? "card card--thumb" : "card"} aria-label={listing.event.name}>
      {thumb && <Poster event={listing.event} size="thumb" />}
      <div className="card__main">
        <div className="card__top">
          <Link className="card__title" to={`/listings/${listing.id}`}>
            {listing.event.name}
          </Link>
          <ListingBadge status={listing.status} />
        </div>
        <p className="meta">
          {formatWhen(listing.event.starts_at)} · {formatPaise(listing.asking_price_paise)} ·
          updated {formatWhen(listing.updated_at)}
        </p>
      </div>
    </li>
  );
}
