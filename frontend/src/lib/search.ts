/**
 * What someone searched for, between the address bar (shareable, survives Back), the search
 * form (rupees) and the API's query (paise).
 */
import type { ListingQuery } from "../api/types";
import { rupeesToPaise } from "./format";

export const SORTS = {
  price: "Price: low to high",
  "-price": "Price: high to low",
  "-created_at": "Newest first",
  created_at: "Oldest first",
} as const;

export type Sort = keyof typeof SORTS;

/** The API's own default, so leaving it out of the address changes nothing. */
export const DEFAULT_SORT: Sort = "price";

// A lowest price of nothing.
const ZERO = /^0+(\.0{1,2})?$/;

export const CATEGORIES = {
  CONCERT: "Concert",
  SPORTS: "Sports",
  COLLEGE: "College fest",
  OTHER: "Other",
} as const;

export type Category = keyof typeof CATEGORIES;

/** The search form's fields, which are also the names used in the address. */
export const FIELDS = ["q", "category", "city", "ticket", "min", "max", "sort"] as const;
export type SearchForm = Partial<Record<(typeof FIELDS)[number], string>>;

/** The search in an address, with anything the API would refuse left out. */
export function formFromParams(params: URLSearchParams): SearchForm {
  const form: SearchForm = {};
  for (const field of FIELDS) {
    const value = params.get(field)?.trim();
    if (value) form[field] = value.slice(0, 100);
  }
  if (form.sort && !Object.hasOwn(SORTS, form.sort)) delete form.sort;
  if (form.category && !Object.hasOwn(CATEGORIES, form.category)) delete form.category;
  if (priceProblem({ min: form.min }).min) delete form.min;
  if (priceProblem({ max: form.max }).max) delete form.max;
  if (priceProblem(form).min) {
    delete form.min; // upside down: neither end can be trusted
    delete form.max;
  }
  return withoutDefaults(form);
}

/** Only what was filled in, so an untouched search has a clean address. */
export function paramsFromForm(form: SearchForm): Record<string, string> {
  return Object.fromEntries(
    FIELDS.flatMap((field) => {
      const value = withoutDefaults(form)[field]?.trim();
      return value ? [[field, value]] : [];
    }),
  );
}

/** Without the default order, and without a lowest price of 0, which asks for nothing. */
function withoutDefaults(form: SearchForm): SearchForm {
  const asked = { ...form };
  if (asked.sort === DEFAULT_SORT) delete asked.sort;
  if (asked.min && ZERO.test(asked.min.trim())) delete asked.min;
  return asked;
}

/** Was anything filtered? The order alone does not narrow a search. */
export function isSearching(form: SearchForm): boolean {
  return FIELDS.some((field) => field !== "sort" && form[field]);
}

/**
 * A listing whose price changed while someone was paging can come back on a later page. Show
 * it once, as it is now.
 */
export function withoutRepeats<T extends { id: string }>(items: T[]): T[] {
  return [...new Map(items.map((item) => [item.id, item])).values()];
}

export function queryFromForm(form: SearchForm): ListingQuery {
  const min = form.min ? rupeesToPaise(form.min) : null;
  const max = form.max ? rupeesToPaise(form.max) : null;
  return {
    ...(form.q ? { q: form.q } : {}),
    ...(form.category ? { category: form.category as Category } : {}),
    ...(form.city ? { city: form.city } : {}),
    ...(form.ticket ? { ticket_category: form.ticket } : {}),
    ...(min !== null ? { min_price_paise: min } : {}),
    ...(max !== null ? { max_price_paise: max } : {}),
    ...(form.sort ? { sort: form.sort as Sort } : {}),
  };
}

/** The most a price filter accepts: ₹1,00,000, the most a ticket may cost. */
const MAX_PRICE_PAISE = 10_000_000;

/** What to fix before searching, by field; empty when nothing is wrong. The API checks again. */
export function priceProblem(form: SearchForm): { min?: string; max?: string } {
  const problems: { min?: string; max?: string } = {};
  for (const end of ["min", "max"] as const) {
    const value = form[end]?.trim();
    if (!value || (end === "min" && ZERO.test(value))) continue;
    const paise = rupeesToPaise(value);
    if (paise === null) problems[end] = "Enter prices in rupees, like 2,000 or 1999.50.";
    else if (paise > MAX_PRICE_PAISE) problems[end] = "No ticket costs more than ₹1,00,000.";
  }
  const min = form.min ? rupeesToPaise(form.min) : null;
  const max = form.max ? rupeesToPaise(form.max) : null;
  if (min !== null && max !== null && min > max) {
    problems.min ??= "The lowest price is above the highest.";
  }
  return problems;
}
