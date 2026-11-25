import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import type { HistoryEntry, Listing, ListingPatch, PublicListing } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { ListingBadge } from "../components/Badges";
import {
  Field,
  FormError,
  Notice,
  SubmitButton,
  errorsByField,
  formValues,
} from "../components/Form";
import { Ticket as TicketCard } from "../components/Ticket";
import {
  formatPaise,
  formatWhen,
  isoToLocalInput,
  localInputToIso,
  paiseToRupeesInput,
} from "../lib/format";
import { checkAskingPrice, historyLabel, isFullListing, sellerActions } from "../lib/listings";
import { isUuid } from "../lib/uuid";
import { NotFoundPage } from "./StatusPages";

/** One listing's cache entries, including its history. Not under "listings" (the lists). */
const listingKey = (listingId: string) => ["listing", listingId] as const;
const historyKey = (listingId: string) => ["listing", listingId, "history"] as const;

type Viewer = "seller" | "admin";

export function ListingPage() {
  const { listingId = "" } = useParams();
  const { status, user } = useAuth();
  const isListingId = isUuid(listingId);
  const listing = useQuery({
    // Who is asking changes the answer (public or full shape), so it is part of the key, and
    // the request waits until we know whether anyone is signed in.
    queryKey: [...listingKey(listingId), user?.id ?? "guest"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/listings/{listing_id}", {
          params: { path: { listing_id: listingId } },
        }),
      ),
    enabled: isListingId && status !== "loading",
    retry: false,
  });

  if (!isListingId) return <NotFoundPage />;
  // A listing that is not on sale and one that does not exist look the same: not found.
  if (listing.error instanceof ApiError && [404, 422].includes(listing.error.problem.status)) {
    return <NotFoundPage />;
  }
  if (listing.error) return <FormError error={listing.error} />;
  if (!listing.data) return null;
  if (!isFullListing(listing.data)) return <ListingCard listing={listing.data} />;
  const viewer: Viewer = listing.data.seller_id === user?.id ? "seller" : "admin";
  return <ManagedListing listing={listing.data} viewer={viewer} />;
}

/** The listing as a ticket: what anyone who can see it sees. */
function ListingCard({
  listing,
  footer,
}: {
  listing: Listing | PublicListing;
  footer?: ReactNode;
}) {
  return (
    <TicketCard
      stub={
        <>
          <h1>Listing</h1>
          <p className="stub__serial" title="Listing ID">
            No. {listing.id.slice(0, 8)}
          </p>
        </>
      }
      footer={footer}
    >
      <p className="pass__name">
        {/* Only a listing on sale is sure to have an event with a public page. */}
        {listing.status === "ACTIVE" ? (
          <Link to={`/events/${listing.event.id}`}>{listing.event.name}</Link>
        ) : (
          listing.event.name
        )}
      </p>
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
          <dt>Asking price</dt>
          <dd>{formatPaise(listing.asking_price_paise)}</dd>
        </div>
        <div>
          <dt>Face value</dt>
          <dd>{formatPaise(listing.original_price_paise)}</dd>
        </div>
        {isFullListing(listing) && (
          <div>
            <dt>Listing ends</dt>
            <dd>{formatWhen(listing.expires_at)}</dd>
          </div>
        )}
      </dl>
    </TicketCard>
  );
}

function ManagedListing({ listing, viewer }: { listing: Listing; viewer: Viewer }) {
  const location = useLocation();
  const submitError = (location.state as { submitError?: string } | null)?.submitError;
  const actions = viewer === "seller" ? sellerActions(listing.status) : [];

  return (
    <>
      <ListingCard
        listing={listing}
        footer={
          viewer === "admin" && (
            <Link to={`/tickets/${listing.ticket_id}`}>View the ticket and its evidence</Link>
          )
        }
      />

      <section className="panel" aria-labelledby="status-heading">
        <h2 id="status-heading">
          Status <ListingBadge status={listing.status} />
        </h2>
        {submitError && listing.status === "DRAFT" && (
          <div className="message message--error" role="alert">
            <p>Saved as a draft, but it couldn't be sent for review: {submitError}</p>
          </div>
        )}
        <StatusSummary listing={listing} />
        <div className="actions">
          {actions.includes("submit") && <SubmitForReview listing={listing} />}
          {(actions.includes("cancel") || (viewer === "admin" && listing.status === "ACTIVE")) && (
            <CancelListing listing={listing} />
          )}
        </div>
      </section>

      {actions.includes("edit") && <ChangePrice listing={listing} />}

      <History listingId={listing.id} viewer={viewer} />
    </>
  );
}

function StatusSummary({ listing }: { listing: Listing }) {
  switch (listing.status) {
    case "DRAFT":
      return <p className="lede">A draft: buyers can't see it until it's reviewed and approved.</p>;
    case "PENDING_VERIFICATION":
      return <p className="lede">Waiting for an admin to review it.</p>;
    case "ACTIVE":
      return (
        <p className="lede">On sale. Buyers can see it until {formatWhen(listing.expires_at)}.</p>
      );
    case "RESERVED":
      return <p className="lede">A buyer is checking out.</p>;
    case "SOLD":
      return <p className="lede">Sold.</p>;
    case "REJECTED":
      return (
        <div className="message message--error" role="status">
          <p>Rejected. It can't be sold.</p>
          {listing.rejection_reason && <p>Reason: {listing.rejection_reason}</p>}
        </div>
      );
    case "CANCELLED":
      return <p className="lede">Cancelled.</p>;
    case "EXPIRED":
      return <p className="lede">Expired.</p>;
  }
}

/** A change to one listing: afterwards, refresh it, its history, and every list of listings. */
function useListingChange<T>(listingId: string, change: (input: T) => Promise<Listing>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: change,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: listingKey(listingId) }),
        queryClient.invalidateQueries({ queryKey: ["listings"] }),
      ]),
  });
}

const pathOf = (listing: Listing) => ({ params: { path: { listing_id: listing.id } } });

function SubmitForReview({ listing }: { listing: Listing }) {
  const submit = useListingChange(listing.id, () =>
    unwrap(api.POST("/api/v1/listings/{listing_id}/submit", pathOf(listing))),
  );
  return (
    <>
      <FormError error={submit.error} />
      <button
        type="button"
        className="button"
        onClick={() => submit.mutate(undefined)}
        disabled={submit.isPending}
        aria-busy={submit.isPending}
      >
        Submit for review
      </button>
    </>
  );
}

function CancelListing({ listing }: { listing: Listing }) {
  const [confirming, setConfirming] = useState(false);
  const cancel = useListingChange(listing.id, () =>
    unwrap(api.DELETE("/api/v1/listings/{listing_id}", pathOf(listing))),
  );

  if (!confirming) {
    return (
      <button type="button" className="button button--quiet" onClick={() => setConfirming(true)}>
        Cancel listing
      </button>
    );
  }
  return (
    <div role="group" aria-label="Confirm cancelling the listing">
      <FormError error={cancel.error} />
      <p>Cancel this listing? This can't be undone.</p>
      <div className="actions">
        <button
          type="button"
          className="button"
          onClick={() => cancel.mutate(undefined)}
          disabled={cancel.isPending}
          aria-busy={cancel.isPending}
        >
          Yes, cancel it
        </button>
        <button type="button" className="link-button" onClick={() => setConfirming(false)}>
          Keep it
        </button>
      </div>
    </div>
  );
}

function ChangePrice({ listing }: { listing: Listing }) {
  // The save lives here, outside the keyed form, so "Saved." outlasts the form's reset.
  const save = useListingChange(listing.id, (body: ListingPatch) =>
    unwrap(api.PATCH("/api/v1/listings/{listing_id}", { ...pathOf(listing), body })),
  );
  return (
    <section className="panel" aria-labelledby="change-heading">
      <h2 id="change-heading">Change price</h2>
      {/* A saved change (or any other) gives the listing a new updated_at, and the form starts
          again from the listing as it now is. */}
      <ChangePriceForm key={listing.updated_at} listing={listing} save={save} />
    </section>
  );
}

function ChangePriceForm({
  listing,
  save,
}: {
  listing: Listing;
  save: ReturnType<typeof useListingChange<ListingPatch>>;
}) {
  const [priceError, setPriceError] = useState<string>();
  const errors = errorsByField(save.error);
  const shownExpiry = isoToLocalInput(listing.expires_at);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formValues(event.currentTarget);
    const price = checkAskingPrice(values.price ?? "", listing.original_price_paise);
    if ("error" in price) {
      setPriceError(price.error);
      return;
    }
    setPriceError(undefined);
    // The expiry is sent only when it was changed: the field shows it to the minute, and
    // sending that back would move it by the seconds it had.
    const expiryChanged = values.expires_at && values.expires_at !== shownExpiry;
    save.mutate({
      asking_price_paise: price.paise,
      ...(expiryChanged ? { expires_at: localInputToIso(values.expires_at!) } : {}),
    });
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Change price">
      <FormError error={save.error} />
      {save.isSuccess && <Notice>Saved.</Notice>}
      <Field
        label="Asking price (₹)"
        name="price"
        inputMode="decimal"
        required
        defaultValue={paiseToRupeesInput(listing.asking_price_paise)}
        hint={
          listing.status === "ACTIVE"
            ? "While the listing is on sale its price can only go down."
            : `At most ${formatPaise(listing.original_price_paise)}, the price you paid.`
        }
        onChange={() => setPriceError(undefined)}
        error={priceError ?? errors.asking_price_paise}
      />
      <Field
        label="Listing ends"
        name="expires_at"
        type="datetime-local"
        required
        defaultValue={shownExpiry}
        hint="No later than the event start."
        error={errors.expires_at}
      />
      <div className="actions">
        <SubmitButton pending={save.isPending}>Save changes</SubmitButton>
      </div>
    </form>
  );
}

const BY: Record<HistoryEntry["actor_role"], Record<Viewer, string>> = {
  seller: { seller: "by you", admin: "by the seller" },
  admin: { seller: "by an admin", admin: "by an admin" },
  system: { seller: "automatically", admin: "automatically" },
};

function History({ listingId, viewer }: { listingId: string; viewer: Viewer }) {
  const history = useQuery({
    queryKey: historyKey(listingId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/listings/{listing_id}/history", {
          params: { path: { listing_id: listingId }, query: { limit: 100 } },
        }),
      ),
  });

  return (
    <section className="panel" aria-labelledby="history-heading">
      <h2 id="history-heading">History</h2>
      <FormError error={history.error} />
      <ol className="list" aria-label="History">
        {history.data?.items.map((entry, index) => (
          <li key={`${entry.created_at}-${index}`} className="card">
            <span className="card__title">
              {historyLabel(entry)} {BY[entry.actor_role][viewer]}
            </span>
            <p className="meta">{formatWhen(entry.created_at)}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
