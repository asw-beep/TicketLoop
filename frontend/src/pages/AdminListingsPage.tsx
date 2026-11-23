import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import type { Listing, ListingStatus } from "../api/types";
import { LISTING_NAMES } from "../components/Badges";
import {
  FormError,
  Notice,
  SelectField,
  TextAreaField,
  errorsByField,
  formValues,
} from "../components/Form";
import { formatPaise, formatWhen } from "../lib/format";
import { ListingRow, STATUSES } from "./MyListingsPage";

/** Under "listings", so a decision here or a change anywhere else refreshes both tabs. */
const PENDING_KEY = ["listings", "admin", "pending"] as const;
const ALL_KEY = ["listings", "admin", "all"] as const;

const TABS = [
  { id: "pending", label: "Pending review" },
  { id: "all", label: "All listings" },
] as const;
type Tab = (typeof TABS)[number]["id"];

/** Listings waiting for a decision, and every listing in every status. */
export function AdminListingsPage() {
  // The open tab lives in the address, so a reload or a shared link keeps it.
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "all" ? "all" : "pending";
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ pending: null, all: null });

  function choose(next: Tab) {
    setParams(next === "pending" ? {} : { tab: next }, { replace: true });
  }

  // Left and right arrows move between the tabs, as in any tab list.
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next: Tab = tab === "pending" ? "all" : "pending";
    choose(next);
    tabRefs.current[next]?.focus();
  }

  return (
    <>
      <div className="page-head">
        <h1>Review listings</h1>
      </div>
      <p className="lede">
        A listing reaches buyers only after an admin approves it. Check it against its ticket and
        the proof of purchase, then decide.
      </p>
      <div className="tabs" role="tablist" aria-label="Listings" onKeyDown={handleKeyDown}>
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            ref={(element) => {
              tabRefs.current[id] = element;
            }}
            type="button"
            role="tab"
            id={`tab-${id}`}
            className="tab"
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            tabIndex={tab === id ? 0 : -1}
            onClick={() => choose(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "pending" ? <PendingTab /> : <AllTab />}
      </div>
    </>
  );
}

function PendingTab() {
  const [last, setLast] = useState<string>();
  const pending = useInfiniteQuery({
    queryKey: PENDING_KEY,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/admin/listings/pending", {
          params: { query: { limit: 20, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const items = pending.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      {last && <Notice>{last}</Notice>}
      <FormError error={pending.error} />
      {pending.isSuccess && items.length === 0 && (
        <p className="empty">Nothing is waiting for review.</p>
      )}
      <ul className="list" aria-label="Listings waiting for review">
        {items.map((listing) => (
          <PendingListing key={listing.id} listing={listing} onDecided={setLast} />
        ))}
      </ul>
      <LoadMore query={pending} />
    </>
  );
}

// These mean someone else got there first: the listing has left the queue.
const GONE = ["listing-not-pending", "listing-changed", "illegal-listing-transition"];

function PendingListing({
  listing,
  onDecided,
}: {
  listing: Listing;
  onDecided: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const name = listing.event.name;
  const path = { params: { path: { listing_id: listing.id } } };
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["listings"] }),
      queryClient.invalidateQueries({ queryKey: ["listing", listing.id] }),
    ]);
  const decide = useMutation({
    mutationFn: (reason: string | null) =>
      unwrap(
        reason === null
          ? api.POST("/api/v1/admin/listings/{listing_id}/approve", path)
          : api.POST("/api/v1/admin/listings/{listing_id}/reject", { ...path, body: { reason } }),
      ),
    onSuccess: async (decided) => {
      onDecided(decided.status === "ACTIVE" ? `Approved: ${name}.` : `Rejected: ${name}.`);
      await refresh();
    },
    onError: async (error) => {
      if (error instanceof ApiError && GONE.includes(error.code)) {
        onDecided(`${name} was already decided or withdrawn.`);
        await refresh();
      }
    },
  });
  const errors = errorsByField(decide.error);
  const expired = new Date(listing.expires_at) <= new Date();

  function handleReject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    decide.mutate(formValues(event.currentTarget).reason ?? "");
  }

  return (
    <li className="card" aria-label={name}>
      <div className="card__top">
        <Link className="card__title" to={`/listings/${listing.id}`}>
          {name}
        </Link>
        <span className="meta">listed {formatWhen(listing.created_at)}</span>
      </div>
      <p className="meta">
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
          <dt>Asking price</dt>
          <dd>{formatPaise(listing.asking_price_paise)}</dd>
        </div>
        <div>
          <dt>Face value</dt>
          <dd>{formatPaise(listing.original_price_paise)}</dd>
        </div>
        <div>
          <dt>Listing ends</dt>
          <dd>
            {formatWhen(listing.expires_at)}{" "}
            {expired && <span className="badge badge--bad">Expired</span>}
          </dd>
        </div>
      </dl>
      <p>
        <Link to={`/tickets/${listing.ticket_id}`}>View the ticket and its evidence</Link>
      </p>
      {expired && (
        <p className="meta">
          Its end date has passed, so it can't be approved unless the seller moves it.
        </p>
      )}
      <FormError error={decide.error} />
      {!rejecting && (
        <div className="actions">
          {!expired && (
            <button
              type="button"
              className="button button--small"
              disabled={decide.isPending}
              aria-busy={decide.isPending}
              onClick={() => decide.mutate(null)}
            >
              Approve
            </button>
          )}
          <button
            type="button"
            className="button button--quiet button--small"
            onClick={() => setRejecting(true)}
          >
            Reject
          </button>
        </div>
      )}
      {rejecting && (
        <form onSubmit={handleReject} aria-label={`Reject ${name}`}>
          <TextAreaField
            id={`reason-${listing.id}`}
            label="Reason"
            name="reason"
            required
            maxLength={500}
            hint="The seller sees this."
            error={errors.reason}
          />
          <div className="actions">
            <button
              type="submit"
              className="button button--small"
              disabled={decide.isPending}
              aria-busy={decide.isPending}
            >
              Reject listing
            </button>
            <button type="button" className="link-button" onClick={() => setRejecting(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </li>
  );
}

function AllTab() {
  const [status, setStatus] = useState<ListingStatus | "">("");
  const listings = useInfiniteQuery({
    queryKey: [...ALL_KEY, status],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/admin/listings", {
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
        <p className="empty">{status ? "No listings with this status." : "No listings yet."}</p>
      )}
      <ul className="list" aria-label="All listings">
        {items.map((listing) => (
          <ListingRow key={listing.id} listing={listing} />
        ))}
      </ul>
      <LoadMore query={listings} />
    </>
  );
}

function LoadMore({
  query,
}: {
  query: { hasNextPage: boolean; isFetchingNextPage: boolean; fetchNextPage: () => unknown };
}) {
  if (!query.hasNextPage) return null;
  return (
    <div className="actions">
      <button
        type="button"
        className="button button--quiet"
        onClick={() => query.fetchNextPage()}
        disabled={query.isFetchingNextPage}
      >
        Load more
      </button>
    </div>
  );
}
