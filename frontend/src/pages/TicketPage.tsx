import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import type { Document, Ticket } from "../api/types";
import { VerificationBadge } from "../components/Badges";
import { FormError, Notice, SubmitButton } from "../components/Form";
import { Ticket as TicketCard } from "../components/Ticket";
import { ACCEPT_ATTRIBUTE, checkFile, openDocument, uploadDocument } from "../lib/documents";
import { formatPaise, formatWhen } from "../lib/format";
import { NotFoundPage } from "./StatusPages";

const registered = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

// A ticket ID is a UUID. Anything else in the address (like "me") is not a ticket, and must
// not reach the API: GET /tickets/me is the list of your tickets, a different shape.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** One ticket's cache entries. Not under "tickets", which holds the list at ["tickets", "me"]. */
export const ticketKey = (ticketId: string) => ["ticket", ticketId] as const;
const documentsKey = (ticketId: string) => ["ticket", ticketId, "documents"] as const;

export function TicketPage() {
  const { ticketId = "" } = useParams();
  const isTicketId = UUID.test(ticketId);
  const ticket = useQuery({
    queryKey: ticketKey(ticketId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets/{ticket_id}", { params: { path: { ticket_id: ticketId } } })),
    enabled: isTicketId,
    retry: false,
  });

  if (!isTicketId) return <NotFoundPage />;

  // Someone else's ticket and a ticket that does not exist look the same: not found.
  // A malformed ID in the address (422) is not a ticket either.
  if (ticket.error instanceof ApiError && [404, 422].includes(ticket.error.problem.status)) {
    return <NotFoundPage />;
  }
  if (ticket.error) return <FormError error={ticket.error} />;
  if (!ticket.data) return null;
  return <TicketDetails ticket={ticket.data} />;
}

function TicketDetails({ ticket }: { ticket: Ticket }) {
  return (
    <>
      <TicketCard
        stub={
          <>
            <h1>Ticket</h1>
            <p className="stub__serial" title="Ticket ID">
              No. {ticket.id.slice(0, 8)}
            </p>
          </>
        }
      >
        <p className="pass__name">{ticket.event.name}</p>
        <p className="pass__email">
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
          <div>
            <dt>Registered</dt>
            <dd>{registered.format(new Date(ticket.created_at))}</dd>
          </div>
        </dl>
      </TicketCard>

      <section className="panel" aria-labelledby="review-heading">
        <h2 id="review-heading">
          Review <VerificationBadge status={ticket.verification_status} />
        </h2>
        <ReviewSummary ticket={ticket} />
      </section>

      <Evidence ticket={ticket} />
    </>
  );
}

function ReviewSummary({ ticket }: { ticket: Ticket }) {
  if (ticket.verification_status === "PENDING") {
    return (
      <p className="lede">
        An admin will check your proof of purchase. Upload it below if you haven't yet.
      </p>
    );
  }
  if (ticket.verification_status === "REJECTED") {
    return (
      <div className="message message--error" role="status">
        <p>This ticket was rejected and can't be resold.</p>
        {ticket.verification_note && <p>Reason: {ticket.verification_note}</p>}
      </div>
    );
  }
  return (
    <Notice>
      <p>
        Verified.{" "}
        {ticket.transfer_eligible
          ? "It can be listed for resale."
          : "Its provider doesn't allow transfers, so it can't be resold."}
      </p>
      {ticket.verification_note && <p>Note: {ticket.verification_note}</p>}
    </Notice>
  );
}

const TYPE_NAMES: Record<Document["content_type"], string> = {
  "application/pdf": "PDF",
  "image/png": "PNG image",
  "image/jpeg": "JPEG image",
};

function size(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The evidence list for an owner or an admin; an owner can add to it while PENDING. */
export function EvidenceList({ ticketId }: { ticketId: string }) {
  const [openError, setOpenError] = useState<unknown>(null);
  const documents = useQuery({
    queryKey: documentsKey(ticketId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/documents", {
          params: { path: { ticket_id: ticketId } },
        }),
      ),
  });

  async function handleOpen(documentId: string) {
    setOpenError(null);
    await openDocument(ticketId, documentId).catch(setOpenError);
  }

  return (
    <>
      <FormError error={documents.error ?? openError} />
      {documents.isSuccess && documents.data.length === 0 && (
        <p className="meta">No proof of purchase uploaded yet.</p>
      )}
      {documents.isSuccess && documents.data.length > 0 && (
        <ul className="list" aria-label="Proof of purchase">
          {documents.data.map((document, index) => (
            <li key={document.id} className="card">
              <div className="card__top">
                <span className="card__title">
                  {TYPE_NAMES[document.content_type]} {index + 1}
                </span>
                <button
                  type="button"
                  className="button button--quiet button--small"
                  onClick={() => handleOpen(document.id)}
                >
                  Open
                </button>
              </div>
              <p className="meta">
                {size(document.size_bytes)} · uploaded {formatWhen(document.created_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Evidence({ ticket }: { ticket: Ticket }) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [checkError, setCheckError] = useState<string>();
  const upload = useMutation({
    mutationFn: (file: File) => uploadDocument(ticket.id, file),
    onSuccess: async () => {
      if (input.current) input.current.value = "";
      await queryClient.invalidateQueries({ queryKey: documentsKey(ticket.id) });
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    upload.reset();
    const file = input.current?.files?.[0];
    if (!file) {
      setCheckError("Choose a file first.");
      return;
    }
    const problem = checkFile(file);
    setCheckError(problem ?? undefined);
    if (!problem) upload.mutate(file);
  }

  return (
    <section className="panel" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading">Proof of purchase</h2>
      <p className="lede">
        Your booking confirmation or e-ticket, as a PDF, PNG or JPEG up to 5 MB. Only you and admins
        can open it, through a link that works for 5 minutes.
      </p>
      <EvidenceList ticketId={ticket.id} />
      {ticket.verification_status === "PENDING" && (
        <form onSubmit={handleSubmit} aria-label="Upload proof of purchase">
          <FormError error={upload.error} />
          {upload.isSuccess && <Notice>Uploaded.</Notice>}
          <div className="field">
            <label htmlFor="evidence-file">File</label>
            <input
              ref={input}
              id="evidence-file"
              name="file"
              type="file"
              accept={ACCEPT_ATTRIBUTE}
              aria-invalid={checkError ? true : undefined}
              aria-describedby={checkError ? "evidence-file-error" : undefined}
              onChange={() => setCheckError(undefined)}
            />
            {checkError && (
              <p className="field__error" id="evidence-file-error">
                {checkError}
              </p>
            )}
          </div>
          <div className="actions">
            <SubmitButton pending={upload.isPending}>Upload</SubmitButton>
          </div>
        </form>
      )}
    </section>
  );
}
