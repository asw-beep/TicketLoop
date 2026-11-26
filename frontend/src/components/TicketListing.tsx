import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, unwrap } from "../api/client";
import { describeError } from "../api/problem";
import type { Listing, ListingIn, Ticket } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { formatPaise, localInputToIso, paiseToRupeesInput } from "../lib/format";
import { OPEN_STATUSES, checkAskingPrice } from "../lib/listings";
import { ListingBadge } from "./Badges";
import { Field, FormError, errorsByField, formValues } from "./Form";

/** Under "listings", so every listing change (which invalidates ["listings"]) refreshes it. */
const ticketListingsKey = (ticketId: string, as: "owner" | "admin") =>
  ["listings", "ticket", ticketId, as] as const;

/**
 * Where a ticket stands for resale: its open listing, or a form to list it. The owner asks
 * for their own listings; an admin looking at someone else's ticket asks the admin list.
 */
export function TicketListing({ ticket }: { ticket: Ticket }) {
  const { user } = useAuth();
  const isOwner = user?.id === ticket.current_owner_id;
  const isAdmin = !isOwner && user?.role === "ADMIN";
  const listings = useQuery({
    queryKey: ticketListingsKey(ticket.id, isOwner ? "owner" : "admin"),
    queryFn: () => {
      const query = { ticket_id: ticket.id, limit: 100 };
      return unwrap(
        isOwner
          ? api.GET("/api/v1/listings/me", { params: { query } })
          : api.GET("/api/v1/admin/listings", { params: { query } }),
      );
    },
    enabled: isOwner || isAdmin,
  });

  if (!isOwner && !isAdmin) return null;
  if (listings.error) return <FormError error={listings.error} />;
  if (!listings.data) return null;

  const open = listings.data.items.find((listing) => OPEN_STATUSES.includes(listing.status));
  if (open) return <OpenListing listing={open} />;
  const listable =
    isOwner &&
    ticket.verification_status === "VERIFIED" &&
    ticket.transfer_eligible &&
    ticket.status === "ACTIVE";
  return listable ? <ListTicketForm ticket={ticket} /> : null;
}

function OpenListing({ listing }: { listing: Listing }) {
  return (
    <section className="panel" aria-labelledby="listing-heading">
      <h2 id="listing-heading">
        Listed <ListingBadge status={listing.status} />
      </h2>
      <p className="lede">Asking {formatPaise(listing.asking_price_paise)}.</p>
      <div className="actions">
        <Link className="button button--quiet" to={`/listings/${listing.id}`}>
          View listing
        </Link>
      </div>
    </section>
  );
}

type ListResult = { listing: Listing; submitError?: string };

function ListTicketForm({ ticket }: { ticket: Ticket }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [priceError, setPriceError] = useState<string>();
  const list = useMutation({
    // Two calls: create the draft, then submit it. If the submit fails, the draft stays and
    // its page says why, so the seller can fix it and submit from there.
    mutationFn: async ({ body, submit }: { body: ListingIn; submit: boolean }) => {
      const listing = await unwrap(api.POST("/api/v1/listings", { body }));
      const result: ListResult = { listing };
      if (submit) {
        await unwrap(
          api.POST("/api/v1/listings/{listing_id}/submit", {
            params: { path: { listing_id: listing.id } },
          }),
        ).catch((error: unknown) => (result.submitError = describeError(error)));
      }
      return result;
    },
    onSuccess: async ({ listing, submitError }) => {
      await queryClient.invalidateQueries({ queryKey: ["listings"] });
      navigate(`/listings/${listing.id}`, { state: submitError ? { submitError } : null });
    },
  });
  const errors = errorsByField(list.error);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const values = formValues(event.currentTarget);
    const price = checkAskingPrice(values.price ?? "", ticket.original_price_paise);
    if ("error" in price) {
      setPriceError(price.error);
      return;
    }
    setPriceError(undefined);
    list.mutate({
      body: {
        ticket_id: ticket.id,
        asking_price_paise: price.paise,
        ...(values.expires_at ? { expires_at: localInputToIso(values.expires_at) } : {}),
      },
      submit: submitter?.value !== "draft",
    });
  }

  return (
    <section className="panel" aria-labelledby="sell-heading">
      <h2 id="sell-heading">Sell this ticket</h2>
      <p className="lede">
        List it for no more than its face value. An admin checks the listing before buyers can see
        it.
      </p>
      <form onSubmit={handleSubmit} aria-label="List this ticket">
        <FormError error={list.error} />
        <Field
          label="Asking price (₹)"
          name="price"
          inputMode="decimal"
          required
          defaultValue={paiseToRupeesInput(ticket.original_price_paise)}
          hint={`At most ${formatPaise(ticket.original_price_paise)}, its face value.`}
          onChange={() => setPriceError(undefined)}
          error={priceError ?? errors.asking_price_paise}
        />
        <Field
          label="Listing ends"
          name="expires_at"
          type="datetime-local"
          hint="Optional. Leave it empty to end the listing when the event starts."
          error={errors.expires_at}
        />
        <div className="actions">
          <button
            className="button"
            type="submit"
            value="submit"
            disabled={list.isPending}
            aria-busy={list.isPending}
          >
            Submit for review
          </button>
          <button
            className="button button--quiet"
            type="submit"
            value="draft"
            disabled={list.isPending}
          >
            Save draft
          </button>
        </div>
      </form>
    </section>
  );
}
