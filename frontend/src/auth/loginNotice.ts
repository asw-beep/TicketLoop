/**
 * A message for the login page, such as "You're logged out."
 *
 * Kept here rather than in navigation state on purpose. Ending a session makes the page's
 * guard redirect to /login at once, and that redirect can replace a navigation that was
 * carrying a message. A message stored here survives whichever redirect wins.
 */
import { useSyncExternalStore } from "react";

let notice: string | null = null;
const listeners = new Set<() => void>();

function publish(next: string | null): void {
  notice = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setLoginNotice(text: string): void {
  publish(text);
}

export function clearLoginNotice(): void {
  publish(null);
}

export function useLoginNotice(): string | null {
  return useSyncExternalStore(subscribe, () => notice);
}
