import { describe, expect, it } from "vitest";
import { retryAfterSeconds, secondsLeft, waitMessage } from "./rateLimit";

describe("retryAfterSeconds", () => {
  it("reads whole seconds", () => {
    expect(retryAfterSeconds("12")).toBe(12);
  });

  it("reads a date, as HTTP also allows", () => {
    const now = Date.parse("Thu, 26 Nov 2026 10:00:00 GMT");
    expect(retryAfterSeconds("Thu, 26 Nov 2026 10:00:30 GMT", now)).toBe(30);
  });

  it.each([null, "", "soon", "-3"])("ignores %j", (header) => {
    expect(retryAfterSeconds(header)).toBeNull();
  });
});

describe("secondsLeft", () => {
  it("counts down from when the answer came, and stops at nothing", () => {
    expect(secondsLeft(12, 1_000, 1_000)).toBe(12);
    expect(secondsLeft(12, 1_000, 5_500)).toBe(8);
    expect(secondsLeft(12, 1_000, 13_000)).toBe(0);
    expect(secondsLeft(12, 1_000, 60_000)).toBe(0);
  });
});

describe("waitMessage", () => {
  it.each([
    [12, "Too many tries. Try again in 12 seconds."],
    [1, "Too many tries. Try again in 1 second."],
    [0, "You can try again now."],
  ])("%i seconds", (seconds, text) => {
    expect(waitMessage(seconds)).toBe(text);
  });
});
