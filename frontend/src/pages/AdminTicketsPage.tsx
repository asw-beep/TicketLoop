import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import type { Ticket, VerifyIn } from "../api/types";
import { FormError, Notice, TextAreaField, errorsByField, formValues } from "../components/Form";
import { formatPaise, formatWhen } from "../lib/format";
import { EvidenceList } from "./TicketPage";

const PENDING_KEY = ["admin", "tickets", "pending"] as const;

/** The queue of tickets waiting for a decision, oldest first. A decided ticket leaves it. */
export function AdminTicketsPage() {
  const [last, setLast] = useState<string>();
  const pending = useInfiniteQuery({
    queryKey: PENDING_KEY,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/admin/tickets/pending", {
          params: { query: { limit: 20, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const items = pending.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Verify tickets</h1>
      </div>
      <p className="lede">
        Oldest first. Check the proof of purchase against the order and ticket numbers, then decide.
        A verified ticket can be resold only if it is transfer eligible.
      </p>
      {last && <Notice>{last}</Notice>}
      <FormError error={pending.error} />
      {pending.isSuccess && items.length === 0 && (
        <p className="empty">Nothing is waiting for review.</p>
      )}
      <ul className="list" aria-label="Tickets waiting for review">
        {items.map((ticket) => (
          <PendingTicket key={ticket.id} ticket={ticket} onDecided={setLast} />
        ))}
      </ul>
      {pending.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => pending.fetchNextPage()}
            disabled={pending.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

function PendingTicket({
  ticket,
  onDecided,
}: {
  ticket: Ticket;
  onDecided: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [transferEligible, setTransferEligible] = useState(true);
  const label = `${ticket.event.name}, ${ticket.ticket_category}`;
  const decide = useMutation({
    mutationFn: (body: VerifyIn) =>
      unwrap(
        api.POST("/api/v1/admin/tickets/{ticket_id}/verify", {
          params: { path: { ticket_id: ticket.id } },
          body,
        }),
      ),
    onSuccess: async (decided) => {
      onDecided(
        decided.verification_status === "VERIFIED" ? `Verified: ${label}.` : `Rejected: ${label}.`,
      );
      await queryClient.invalidateQueries({ queryKey: PENDING_KEY });
    },
    onError: async (error) => {
      // Another admin decided it first: it is no longer in the queue.
      if (error instanceof ApiError && error.problem.status === 409) {
        onDecided(`${label} was already decided by another admin.`);
        await queryClient.invalidateQueries({ queryKey: PENDING_KEY });
      }
    },
  });
  const errors = errorsByField(decide.error);

  function handleReject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const { note } = formValues(event.currentTarget);
    decide.mutate({ decision: "REJECTED", transfer_eligible: false, note: note! });
  }

  return (
    <li className="card" aria-label={ticket.event.name}>
      <div className="card__top">
        <span className="card__title">{ticket.event.name}</span>
        <span className="meta">registered {formatWhen(ticket.created_at)}</span>
      </div>
      <p className="meta">
        {ticket.event.venue}, {ticket.event.city} · {formatWhen(ticket.event.starts_at)}
      </p>
      <dl className="details">
        <div>
          <dt>Category</dt>
          <dd>{ticket.ticket_category}</dd>
        </div>
        <div>
          <dt>Seat</dt>
          <dd>{ticket.seat_details ?? "Not given"}</dd>
        </div>
        <div>
          <dt>Price paid</dt>
          <dd>{formatPaise(ticket.original_price_paise)}</dd>
        </div>
        <div>
          <dt>Order number</dt>
          <dd className="code">{ticket.purchase_reference}</dd>
        </div>
        <div>
          <dt>Ticket number</dt>
          <dd className="code">{ticket.provider_reference}</dd>
        </div>
      </dl>
      <EvidenceList ticketId={ticket.id} />
      <FormError error={decide.error} />
      {!rejecting && (
        <div className="actions">
          <label className="check">
            <input
              type="checkbox"
              checked={transferEligible}
              onChange={(e) => setTransferEligible(e.target.checked)}
            />
            Transfer eligible
          </label>
          <button
            type="button"
            className="button button--small"
            disabled={decide.isPending}
            onClick={() =>
              decide.mutate({ decision: "VERIFIED", transfer_eligible: transferEligible })
            }
          >
            Verify
          </button>
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
        <form onSubmit={handleReject} aria-label={`Reject ${ticket.event.name}`}>
          <TextAreaField
            id={`note-${ticket.id}`}
            label="Reason"
            name="note"
            required
            maxLength={500}
            hint="The ticket's owner sees this."
            error={errors.note}
          />
          <div className="actions">
            <button
              type="submit"
              className="button button--small"
              disabled={decide.isPending}
              aria-busy={decide.isPending}
            >
              Reject ticket
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
