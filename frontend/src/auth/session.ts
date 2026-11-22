/**
 * The access token, kept in memory only, and the refresh that renews it.
 *
 * The access token lives in this module's variable: not in localStorage, so a script that
 * gets into the page cannot carry it away, and it dies with the tab. The refresh token is
 * an HttpOnly cookie the browser sends to /api/v1/auth by itself; this code never sees it.
 * A reload therefore starts with no access token, and the app calls refresh to get one.
 */
import type { components } from "../api/schema";

type AccessTokenOut = components["schemas"]["AccessTokenOut"];
type Listener = (token: string | null) => void;

const REFRESH_URL = "/api/v1/auth/refresh";
const REFRESH_LOCK = "ticketloop:refresh";

let accessToken: string | null = null;
const listeners = new Set<Listener>();
let inFlight: Promise<string | null> | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  for (const listener of listeners) listener(token);
}

export function onAccessTokenChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Swap the refresh cookie for a new access token. Null means there is no session.
 *
 * Every refresh spends the cookie's token and sets a new one, and the server treats a spent
 * token coming back as theft: it ends the whole session. So refreshes must never overlap.
 * Inside this tab, concurrent callers share one request. Across tabs, a Web Lock makes each
 * tab wait its turn, and the browser then sends the cookie the previous tab just received.
 */
export function refreshAccessToken(): Promise<string | null> {
  inFlight ??= withRefreshLock(requestAccessToken).finally(() => {
    inFlight = null;
  });
  return inFlight;
}

function withRefreshLock(task: () => Promise<string | null>): Promise<string | null> {
  const locks = globalThis.navigator?.locks; // missing in old browsers and in jsdom
  if (!locks) return task();
  // The DOM types say Promise<Promise<...>>; at runtime the lock resolves to the task's value.
  return locks.request(REFRESH_LOCK, task) as unknown as Promise<string | null>;
}

async function requestAccessToken(): Promise<string | null> {
  const response = await fetch(REFRESH_URL, { method: "POST", credentials: "same-origin" });
  if (response.status === 401 || response.status === 403) {
    setAccessToken(null); // no cookie, or it was revoked, expired or suspended
    return null;
  }
  if (!response.ok) throw new Error(`Refreshing the session failed with status ${response.status}`);
  const body = (await response.json()) as AccessTokenOut;
  setAccessToken(body.access_token);
  return body.access_token;
}
