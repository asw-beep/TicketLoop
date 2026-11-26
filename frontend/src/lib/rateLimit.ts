/**
 * The API's 429: how long it says to wait (Retry-After), and the words for it. One message,
 * the same on every form that can be refused: log in, search, reserve, pay.
 */

/** Retry-After as seconds: a number of seconds, or a date (HTTP allows both). */
export function retryAfterSeconds(header: string | null, now: number = Date.now()): number | null {
  const text = header?.trim() ?? "";
  if (/^\d+$/.test(text)) return Number(text);
  // An HTTP date names its day and month; anything else ("-3", "soon") is not one.
  if (!/[a-z]{3}/i.test(text)) return null;
  const when = Date.parse(text);
  if (Number.isNaN(when)) return null;
  return Math.max(0, Math.ceil((when - now) / 1000));
}

/** Seconds still to wait, counting from when the refusal arrived. */
export function secondsLeft(retryAfter: number, receivedAt: number, now: number): number {
  return Math.max(0, Math.ceil(retryAfter - (now - receivedAt) / 1000));
}

export function waitMessage(seconds: number): string {
  if (seconds <= 0) return "You can try again now.";
  return `Too many tries. Try again in ${seconds} ${seconds === 1 ? "second" : "seconds"}.`;
}
