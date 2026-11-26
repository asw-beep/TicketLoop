/**
 * The API's errors: RFC 9457 problem details (application/problem+json), as built by
 * app/core/exceptions.py. OpenAPI does not describe them yet, so the type is written here.
 */

const TYPE_PREFIX = "urn:ticketloop:problem:";

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  request_id?: string;
  errors?: { location: (string | number)[]; message: string; type: string }[];
}

/** An error response from the API, thrown so that forms and queries can show it. */
export class ApiError extends Error {
  readonly problem: Problem;
  /** Seconds to wait, from a 429's Retry-After; null when the answer did not say. */
  readonly retryAfter: number | null;
  /** When the answer arrived, so a countdown can start from it. */
  readonly receivedAt: number;

  constructor(problem: Problem, retryAfter: number | null = null) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.problem = problem;
    this.retryAfter = retryAfter;
    this.receivedAt = Date.now();
  }

  get code(): string {
    return problemCode(this.problem);
  }
}

export function problemCode(problem: Problem): string {
  return problem.type.startsWith(TYPE_PREFIX)
    ? problem.type.slice(TYPE_PREFIX.length)
    : problem.type;
}

/** Validation messages keyed by body field, e.g. {password: "String should have ..."}. */
export function fieldErrors(problem: Problem | undefined): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const error of problem?.errors ?? []) {
    const [where, field] = error.location;
    if (where === "body" && typeof field === "string") fields[field] ??= error.message;
  }
  return fields;
}

/** One sentence for the person using the page. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) {
    return "Couldn't reach the TicketLoop API. Check that it is running on port 8000.";
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

/** An ApiError for a response whose body was not a problem: a proxy page, or nothing. */
export function errorFor(status: number): ApiError {
  return new ApiError({
    type: "about:blank",
    title: `The API answered with status ${status}. Check that it is running.`,
    status,
  });
}
