/**
 * The typed API client. Paths, bodies and replies come from schema.d.ts, which
 * `npm run api:types` generates from the backend, so a renamed field fails the type check.
 */
import createClient from "openapi-fetch";
import { getAccessToken, refreshAccessToken } from "../auth/session";
import { noteServerDate } from "../lib/clock";
import { retryAfterSeconds } from "../lib/rateLimit";
import { ApiError, errorFor, type Problem } from "./problem";
import type { paths } from "./schema";

const NOT_AUTHENTICATED = "urn:ticketloop:problem:not-authenticated";

/**
 * fetch with the access token attached. When the token has expired, refresh it once and
 * send the request again; when the session has ended, give the caller the 401.
 */
export async function authFetch(request: Request): Promise<Response> {
  const sentToken = getAccessToken();
  const retry = request.clone(); // a body can be read only once, so keep a copy for the retry
  const response = await fetch(withToken(request, sentToken));
  noteServerDate(response.headers.get("Date"));
  if (!sentToken || !(await isExpiredToken(response))) return response;

  const freshToken = await refreshAccessToken();
  return freshToken ? fetch(withToken(retry, freshToken)) : response;
}

function withToken(request: Request, token: string | null): Request {
  if (token) request.headers.set("Authorization", `Bearer ${token}`);
  return request;
}

/** Only a 401 about the token means "refresh". A wrong current password is a 401 too. */
async function isExpiredToken(response: Response): Promise<boolean> {
  if (response.status !== 401) return false;
  const body = await response
    .clone()
    .json()
    .catch(() => null);
  return body?.type === NOT_AUTHENTICATED;
}

// Same origin as the page: the dev server and, later, the production proxy forward /api.
export const api = createClient<paths>({ baseUrl: globalThis.location?.origin, fetch: authFetch });

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Await an api call: its data on success, an ApiError on failure. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (error === undefined && response.ok) return data as T;
  // openapi-fetch has already read and parsed the body into `error`; it cannot be read twice.
  if (isProblem(error)) {
    throw new ApiError(error, retryAfterSeconds(response.headers.get("Retry-After")));
  }
  throw errorFor(response.status);
}

function isProblem(value: unknown): value is Problem {
  return typeof value === "object" && value !== null && "type" in value && "title" in value;
}
