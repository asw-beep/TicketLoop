import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/problem";
import { Feature } from "../components/Feature";
import { FormError, SelectField } from "../components/Form";
import { ListingResults } from "../components/ListingResults";
import { Ticket } from "../components/Ticket";
import { formatWhen } from "../lib/format";
import { CATEGORIES, SORTS, type Sort } from "../lib/search";
import { isUuid } from "../lib/uuid";
import { NotFoundPage } from "./StatusPages";

/** One event and the tickets on sale for it. */
export function EventPage() {
  const { eventId = "" } = useParams();
  const event = useQuery({
    queryKey: ["event", eventId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/events/{event_id}", { params: { path: { event_id: eventId } } })),
    enabled: isUuid(eventId),
    retry: false,
  });

  if (!isUuid(eventId)) return <NotFoundPage />;
  if (event.error instanceof ApiError && event.error.problem.status === 404) {
    return <NotFoundPage />;
  }
  if (event.error) return <FormError error={event.error} />;
  if (!event.data) return null;

  return (
    <div className="wide">
      <Feature poster={event.data}>
        <Ticket stub={<h1>Event</h1>}>
          <p className="pass__name">{event.data.name}</p>
          <p className="pass__email">
            {event.data.venue}, {event.data.city} · {formatWhen(event.data.starts_at)}
          </p>
          {event.data.description && <p>{event.data.description}</p>}
          <dl className="details">
            <div>
              <dt>Category</dt>
              <dd>{CATEGORIES[event.data.category]}</dd>
            </div>
            <div>
              <dt>Organizer</dt>
              <dd>{event.data.organizer}</dd>
            </div>
          </dl>
        </Ticket>
      </Feature>
      <EventListings eventId={eventId} />
    </div>
  );
}

function EventListings({ eventId }: { eventId: string }) {
  const [sort, setSort] = useState<Sort>("price");
  const results = useInfiniteQuery({
    queryKey: ["search", "event", eventId, sort],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/events/{event_id}/listings", {
          params: {
            path: { event_id: eventId },
            query: { sort, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });

  return (
    <section className="panel panel--wide" aria-labelledby="listings-heading">
      <h2 id="listings-heading">Tickets on sale</h2>
      <SelectField
        label="Sort by"
        name="sort"
        value={sort}
        onChange={(event) => setSort(event.target.value as Sort)}
      >
        {Object.entries(SORTS).map(([value, name]) => (
          <option key={value} value={value}>
            {name}
          </option>
        ))}
      </SelectField>
      <ListingResults
        results={results}
        showEvent={false}
        empty="No tickets are on sale for this event right now."
      />
    </section>
  );
}
