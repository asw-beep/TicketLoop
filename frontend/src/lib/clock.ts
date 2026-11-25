/**
 * What time the server thinks it is. A hold ends by the server's clock, and a laptop's can be
 * minutes off: counting down by the browser's would end a hold early, or promise time that has
 * gone. Every response says when it was sent (its Date header), so the difference is known.
 */

// Date has whole seconds and the response took a moment, so a right clock looks a little off.
const TOLERANCE_MS = 2_000;

let aheadMs = 0; // how far the browser's clock is ahead of the server's

export function noteServerDate(header: string | null, browserNow: number = Date.now()): void {
  const serverTime = Date.parse(header ?? "");
  if (Number.isNaN(serverTime)) return;
  const difference = browserNow - serverTime;
  aheadMs = Math.abs(difference) <= TOLERANCE_MS ? 0 : difference;
}

export function serverNow(browserNow: number = Date.now()): number {
  return browserNow - aheadMs;
}
