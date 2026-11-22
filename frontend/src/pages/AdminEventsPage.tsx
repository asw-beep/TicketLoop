import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, unwrap } from "../api/client";
import type {
  Event,
  EventCategory,
  EventIn,
  EventPatch,
  EventStatus,
  Provider,
} from "../api/types";
import {
  Field,
  FormError,
  SelectField,
  SubmitButton,
  TextAreaField,
  errorsByField,
  formValues,
} from "../components/Form";
import { formatWhen, isoToLocalInput, localInputToIso } from "../lib/format";

const EVENTS_KEY = ["admin", "events"] as const;

export const CATEGORY_NAMES: Record<EventCategory, string> = {
  CONCERT: "Concert",
  SPORTS: "Sports",
  COLLEGE: "College",
  OTHER: "Other",
};

export const EVENT_STATUS_NAMES: Record<EventStatus, string> = {
  DRAFT: "Draft",
  PUBLISHED: "Published",
  CANCELLED: "Cancelled",
  COMPLETED: "Completed",
};

const BADGE: Record<EventStatus, string> = {
  DRAFT: "badge",
  PUBLISHED: "badge badge--live",
  COMPLETED: "badge badge--ok",
  CANCELLED: "badge badge--bad",
};

/** The status buttons each status offers. The API enforces the rules; this only hides moves
 * it would refuse. */
const MOVES: Record<EventStatus, { to: EventStatus; label: string }[]> = {
  DRAFT: [
    { to: "PUBLISHED", label: "Publish" },
    { to: "CANCELLED", label: "Cancel event" },
  ],
  PUBLISHED: [
    { to: "COMPLETED", label: "Mark completed" },
    { to: "CANCELLED", label: "Cancel event" },
  ],
  CANCELLED: [],
  COMPLETED: [],
};

export function AdminEventsPage() {
  const [creating, setCreating] = useState(false);
  const events = useInfiniteQuery({
    queryKey: EVENTS_KEY,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/admin/events", {
          params: { query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });
  const items = events.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <div className="page-head">
        <h1>Events</h1>
        {!creating && (
          <button type="button" className="button" onClick={() => setCreating(true)}>
            New event
          </button>
        )}
      </div>

      {creating && (
        <section className="panel" aria-labelledby="new-event-heading">
          <h2 id="new-event-heading">New event</h2>
          <EventForm onDone={() => setCreating(false)} />
        </section>
      )}

      <FormError error={events.error} />
      {events.isSuccess && items.length === 0 && <p className="empty">No events yet.</p>}
      <ul className="list" aria-label="Events">
        {items.map((event) => (
          <EventCard key={event.id} event={event} />
        ))}
      </ul>
      {events.hasNextPage && (
        <div className="actions">
          <button
            type="button"
            className="button button--quiet"
            onClick={() => events.fetchNextPage()}
            disabled={events.isFetchingNextPage}
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}

function EventCard({ event }: { event: Event }) {
  const [editing, setEditing] = useState(false);
  const queryClient = useQueryClient();
  const move = useMutation({
    mutationFn: (status: EventStatus) =>
      unwrap(
        api.PATCH("/api/v1/admin/events/{event_id}", {
          params: { path: { event_id: event.id } },
          body: { status },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: EVENTS_KEY }),
  });
  const editable = event.status === "DRAFT" || event.status === "PUBLISHED";

  return (
    <li className="card" aria-label={event.name}>
      <div className="card__top">
        <span className="card__title">{event.name}</span>
        <span className={BADGE[event.status]}>{EVENT_STATUS_NAMES[event.status]}</span>
      </div>
      <p className="meta">
        {CATEGORY_NAMES[event.category]} · {event.venue}, {event.city} ·{" "}
        {formatWhen(event.starts_at)}
      </p>
      <FormError error={move.error} />
      {!editing && (
        <div className="actions">
          {editable && (
            <button
              type="button"
              className="button button--quiet button--small"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
          )}
          {MOVES[event.status].map(({ to, label }) => (
            <button
              key={to}
              type="button"
              className={`button button--small${to === "CANCELLED" ? " button--quiet" : ""}`}
              disabled={move.isPending}
              onClick={() => move.mutate(to)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {editing && <EventForm event={event} onDone={() => setEditing(false)} />}
    </li>
  );
}

const TEXT_FIELDS = ["name", "description", "venue", "city", "organizer"] as const;

/** Create an event, or edit one: an edit sends only the fields that changed. */
function EventForm({ event, onDone }: { event?: Event; onDone: () => void }) {
  const queryClient = useQueryClient();
  const providers = useQuery({
    queryKey: ["admin", "providers"],
    queryFn: () => unwrap(api.GET("/api/v1/admin/providers")),
  });
  const save = useMutation({
    mutationFn: (values: Record<string, string>) => {
      const body = {
        name: values.name!,
        description: values.description ?? "",
        category: values.category as EventCategory,
        venue: values.venue!,
        city: values.city!,
        organizer: values.organizer!,
        starts_at: localInputToIso(values.starts_at!),
        provider_id: values.provider_id!,
      } satisfies EventIn;
      if (!event) return unwrap(api.POST("/api/v1/admin/events", { body }));
      return unwrap(
        api.PATCH("/api/v1/admin/events/{event_id}", {
          params: { path: { event_id: event.id } },
          body: changedFields(event, body),
        }),
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: EVENTS_KEY });
      onDone();
    },
  });
  const errors = errorsByField(save.error);
  const providerLocked = event !== undefined && event.status !== "DRAFT";

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    save.mutate(formValues(e.currentTarget));
  }

  const prefix = event ? `edit-${event.id.slice(0, 8)}-` : "new-";
  return (
    <form onSubmit={handleSubmit} aria-label={event ? `Edit ${event.name}` : "New event"}>
      <FormError error={save.error ?? providers.error} />
      <Field
        id={`${prefix}name`}
        label="Name"
        name="name"
        required
        maxLength={200}
        defaultValue={event?.name}
        error={errors.name}
      />
      <TextAreaField
        id={`${prefix}description`}
        label="Description"
        name="description"
        maxLength={5000}
        defaultValue={event?.description}
        error={errors.description}
      />
      <SelectField
        id={`${prefix}category`}
        label="Category"
        name="category"
        defaultValue={event?.category ?? "CONCERT"}
        error={errors.category}
      >
        {Object.entries(CATEGORY_NAMES).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </SelectField>
      <Field
        id={`${prefix}venue`}
        label="Venue"
        name="venue"
        required
        maxLength={200}
        defaultValue={event?.venue}
        error={errors.venue}
      />
      <Field
        id={`${prefix}city`}
        label="City"
        name="city"
        required
        maxLength={100}
        defaultValue={event?.city}
        error={errors.city}
      />
      <Field
        id={`${prefix}starts_at`}
        label="Starts at"
        name="starts_at"
        type="datetime-local"
        required
        defaultValue={event ? isoToLocalInput(event.starts_at) : undefined}
        hint="Your local time."
        error={errors.starts_at}
      />
      <Field
        id={`${prefix}organizer`}
        label="Organizer"
        name="organizer"
        required
        maxLength={200}
        defaultValue={event?.organizer}
        error={errors.organizer}
      />
      <ProviderSelect
        id={`${prefix}provider`}
        providers={providers.data ?? []}
        defaultValue={event?.provider_id}
        locked={providerLocked}
        error={errors.provider_id}
      />
      <div className="actions">
        <SubmitButton pending={save.isPending}>
          {event ? "Save changes" : "Create event"}
        </SubmitButton>
        <button type="button" className="link-button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ProviderSelect({
  id,
  providers,
  defaultValue,
  locked,
  error,
}: {
  id: string;
  providers: Provider[];
  defaultValue?: string;
  locked: boolean;
  error?: string;
}) {
  if (providers.length === 0) return null; // the select appears once the list has loaded
  if (locked && defaultValue) {
    const name = providers.find((p) => p.id === defaultValue)?.name ?? "Unknown";
    return (
      <div className="field">
        <span className="field__label">Ticket provider</span>
        <p className="meta">{name}. Fixed once the event is published.</p>
        <input type="hidden" name="provider_id" value={defaultValue} />
      </div>
    );
  }
  const fallback = providers.find((p) => p.code === "fakeprovider")?.id ?? providers[0]!.id;
  return (
    <SelectField
      id={id}
      label="Ticket provider"
      name="provider_id"
      defaultValue={defaultValue ?? fallback}
      error={error}
    >
      {providers.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </SelectField>
  );
}

/** Only what differs from the event as loaded, so an edit never resends untouched fields. */
function changedFields(event: Event, next: EventIn): EventPatch {
  const patch: EventPatch = {};
  for (const key of TEXT_FIELDS) if (next[key] !== event[key]) patch[key] = next[key];
  if (next.category !== event.category) patch.category = next.category;
  if (new Date(next.starts_at).getTime() !== new Date(event.starts_at).getTime()) {
    patch.starts_at = next.starts_at;
  }
  if (next.provider_id !== event.provider_id) patch.provider_id = next.provider_id;
  return patch;
}
