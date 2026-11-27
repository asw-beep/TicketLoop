import { useInfiniteQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useLocation, useNavigationType, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";
import { Field, SelectField, formValues } from "../components/Form";
import { ListingResults } from "../components/ListingResults";
import { Poster } from "../components/Poster";
import { HOUSE_POSTER } from "../lib/poster";
import {
  CATEGORIES,
  DEFAULT_SORT,
  SORTS,
  formFromParams,
  isSearching,
  paramsFromForm,
  priceProblem,
  queryFromForm,
  type SearchForm,
} from "../lib/search";

/** The front page: every ticket on sale. The address holds the search, so it can be shared. */
export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const formKey = useFormKey();
  const form = formFromParams(params);
  const query = queryFromForm(form);
  const results = useInfiniteQuery({
    queryKey: ["search", query],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/listings", {
          params: { query: { ...query, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });

  function search(next: SearchForm, typed: boolean) {
    const asked = new URLSearchParams(paramsFromForm(next)).toString();
    // The same search again is a refresh, not another step for Back to go through.
    if (asked === new URLSearchParams(paramsFromForm(form)).toString()) void results.refetch();
    else setParams(asked, { state: { typed } });
  }

  return (
    <div className="wide">
      <section className="hero">
        <div className="hero__text">
          <h1>Find tickets</h1>
          <p className="hero__headline">Face value. Never a rupee more.</p>
          <p className="lede">Every ticket here was checked by an admin before it went on sale.</p>
        </div>
        <Poster event={HOUSE_POSTER} size="hero" />
      </section>
      <Filters
        key={formKey}
        form={form}
        onSearch={(next) => search(next, true)}
        onClear={() => search({}, false)}
      />
      <ListingResults
        results={results}
        empty={
          isSearching(form)
            ? "No tickets match. Try fewer filters."
            : "No tickets are on sale right now."
        }
      />
    </div>
  );
}

/**
 * A key that changes when the form must be refilled from the address: Back, Forward, a link, or
 * Clear. Not when the form itself changed the address. Rebuilding the form then would throw
 * away the keyboard focus of whoever just pressed Enter in it.
 */
function useFormKey(): number {
  const location = useLocation();
  const cameFromForm =
    useNavigationType() !== "POP" && (location.state as { typed?: boolean } | null)?.typed === true;
  const [seen, setSeen] = useState(location.key);
  // Counted, not the location's own key: Back to the first entry has the key it started with.
  const [formKey, setFormKey] = useState(0);
  if (location.key !== seen) {
    setSeen(location.key);
    if (!cameFromForm) setFormKey((count) => count + 1);
  }
  return formKey;
}

function Filters({
  form,
  onSearch,
  onClear,
}: {
  form: SearchForm;
  onSearch: (form: SearchForm) => void;
  onClear: () => void;
}) {
  const [problems, setProblems] = useState<{ min?: string; max?: string }>({});

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = formValues(event.currentTarget) as SearchForm;
    const found = priceProblem(values);
    setProblems(found);
    if (!found.min && !found.max) onSearch(values);
  }

  return (
    <form className="filters" role="search" aria-label="Search tickets" onSubmit={handleSubmit}>
      <div className="filters__wide">
        <Field
          label="Event"
          name="q"
          type="search"
          maxLength={100}
          placeholder="Part of the name"
          defaultValue={form.q}
        />
      </div>
      <SelectField label="Category" name="category" defaultValue={form.category ?? ""}>
        <option value="">Any</option>
        {Object.entries(CATEGORIES).map(([value, name]) => (
          <option key={value} value={value}>
            {name}
          </option>
        ))}
      </SelectField>
      <Field label="City" name="city" maxLength={100} defaultValue={form.city} />
      <Field
        label="Ticket type"
        name="ticket"
        maxLength={100}
        placeholder="VIP"
        defaultValue={form.ticket}
      />
      <Field
        label="Lowest price (₹)"
        name="min"
        inputMode="decimal"
        defaultValue={form.min}
        error={problems.min}
      />
      <Field
        label="Highest price (₹)"
        name="max"
        inputMode="decimal"
        defaultValue={form.max}
        error={problems.max}
      />
      <SelectField label="Sort by" name="sort" defaultValue={form.sort ?? DEFAULT_SORT}>
        {Object.entries(SORTS).map(([value, name]) => (
          <option key={value} value={value}>
            {name}
          </option>
        ))}
      </SelectField>
      <div className="filters__wide actions">
        <button className="button" type="submit">
          Search
        </button>
        {isSearching(form) && (
          <button type="button" className="link-button" onClick={onClear}>
            Clear filters
          </button>
        )}
      </div>
    </form>
  );
}
