import { afterEach, describe, expect, it } from "vitest";
import { noteServerDate, serverNow } from "./clock";

const NOON = Date.parse("2026-11-26T12:00:00Z");

describe("the server's clock", () => {
  afterEach(() => noteServerDate(new Date(0).toUTCString(), 0)); // back to no difference

  it("follows a browser clock that is wrong by minutes", () => {
    // The browser thinks it is 12:07; the server says 12:00.
    const browser = NOON + 7 * 60_000;
    noteServerDate("Thu, 26 Nov 2026 12:00:00 GMT", browser);
    expect(serverNow(browser)).toBe(NOON);
    expect(serverNow(browser + 30_000)).toBe(NOON + 30_000);
  });

  it("keeps what it knew when a response has no usable Date", () => {
    noteServerDate("Thu, 26 Nov 2026 12:00:00 GMT", NOON + 60_000);
    noteServerDate(null, NOON);
    noteServerDate("yesterday-ish", NOON);
    expect(serverNow(NOON + 60_000)).toBe(NOON);
  });

  it("does not jump about for differences under a second or two", () => {
    // Date has whole seconds, so a right clock always looks up to a second off.
    noteServerDate("Thu, 26 Nov 2026 12:00:00 GMT", NOON + 900);
    expect(serverNow(NOON + 900)).toBe(NOON + 900);
  });
});
