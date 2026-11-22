import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, unwrap } from "../api/client";
import type { Ticket } from "../api/types";
import { VerificationBadge } from "../components/Badges";
import {
  Field,
  FormError,
  SelectField,
  SubmitButton,
  errorsByField,
  formValues,
} from "../components/Form";
import { formatPaise, formatWhen, rupeesToPaise } from "../lib/format";

export const MY_TICKETS_KEY = ["tickets", "me"] as const;

export function MyTicketsPage() {
  const [registering, setRegistering] = useState(false);
  const tickets = useInfiniteQuery({
    queryKey: MY_TICKETS_KEY,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/tickets/me", {
          params: { query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const items = tickets.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>My tickets</h1>
        {!registering && (
          <button type="button" className="button" onClick={() => setRegistering(true)}>
            Register a ticket
          </button>
        )}
      </div>
      <p className="lede">
        Register a ticket you bought, then upload proof of purchase. An admin checks it before it
        can be resold.
      </p>

      {registering && <RegisterTicket onCancel={() => setRegistering(false)} />}

      <FormError error={tickets.error} />
      {tickets.isSuccess && items.length === 0 && !registering && (
        <p className="empty">You haven't registered any tickets yet.</p>
      )}
      <ul className="list" aria-label="Your tickets">
        {items.map((ticket) => (
          <TicketCard key={ticket.id} ticket={ticket} />
        ))}
      </ul>
      {tickets.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => tickets.fetchNextPage()}
            disabled={tickets.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

function TicketCard({ ticket }: { ticket: Ticket }) {
  return (
    <li className="card">
      <div className="card__top">
        <Link className="card__title" to={`/tickets/${ticket.id}`}>
          {ticket.event.name}
        </Link>
        <VerificationBadge status={ticket.verification_status} />
      </div>
      <p className="meta">
        {ticket.ticket_category}
        {ticket.seat_details ? ` · ${ticket.seat_details}` : ""} ·{" "}
        {formatPaise(ticket.original_price_paise)} · {formatWhen(ticket.event.starts_at)}
      </p>
    </li>
  );
}

function RegisterTicket({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const [priceError, setPriceError] = useState<string>();
  const events = useQuery({
    queryKey: ["events", "open"],
    queryFn: () => unwrap(api.GET("/api/v1/events", { params: { query: { limit: 100 } } })),
  });
  const register = useMutation({
    mutationFn: (body: {
      event_id: string;
      ticket_category: string;
      seat_details: string | null;
      original_price_paise: number;
      purchase_reference: string;
      provider_reference: string;
    }) => unwrap(api.POST("/api/v1/tickets", { body })),
    onSuccess: (ticket) => navigate(`/tickets/${ticket.id}`),
  });
  const errors = errorsByField(register.error);
  const open = events.data?.items ?? [];

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formValues(event.currentTarget);
    const paise = rupeesToPaise(values.price ?? "");
    if (paise === null) {
      setPriceError("Enter the price you paid in rupees, like 2,500 or 1499.50.");
      return;
    }
    setPriceError(undefined);
    register.mutate({
      event_id: values.event_id!,
      ticket_category: values.ticket_category!,
      seat_details: values.seat_details?.trim() ? values.seat_details : null,
      original_price_paise: paise,
      purchase_reference: values.purchase_reference!,
      provider_reference: values.provider_reference!,
    });
  }

  return (
    <section className="panel" aria-labelledby="register-heading">
      <h2 id="register-heading">Register a ticket</h2>
      <FormError error={events.error} />
      {events.isSuccess && open.length === 0 ? (
        <>
          <p className="lede">No events are open for registration yet.</p>
          <div className="actions">
            <button type="button" className="link-button" onClick={onCancel}>
              Close
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={handleSubmit} aria-label="Register a ticket">
          <FormError error={register.error} />
          <SelectField label="Event" name="event_id" required error={errors.event_id}>
            {open.map((event) => (
              <option key={event.id} value={event.id}>
                {event.name} · {event.city} · {formatWhen(event.starts_at)}
              </option>
            ))}
          </SelectField>
          <Field
            label="Ticket category"
            name="ticket_category"
            required
            maxLength={50}
            hint="As printed on the ticket, like GENERAL or VIP."
            error={errors.ticket_category}
          />
          <Field
            label="Seat or section"
            name="seat_details"
            maxLength={200}
            hint="Optional."
            error={errors.seat_details}
          />
          <Field
            label="Price you paid (₹)"
            name="price"
            inputMode="decimal"
            required
            hint="The ticket's face value, without booking fees."
            onChange={() => setPriceError(undefined)}
            error={priceError ?? errors.original_price_paise}
          />
          <Field
            label="Order or booking number"
            name="purchase_reference"
            required
            maxLength={100}
            hint="From your purchase confirmation. Only you and admins see it."
            error={errors.purchase_reference}
          />
          <Field
            label="Ticket number"
            name="provider_reference"
            required
            maxLength={100}
            hint="The ticket's own ID from the provider. Only you and admins see it."
            error={errors.provider_reference}
          />
          <div className="actions">
            <SubmitButton pending={register.isPending}>Register ticket</SubmitButton>
            <button type="button" className="link-button" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
