import { describe, expect, it } from "vitest";
import { formatPaise, isoToLocalInput, localInputToIso, rupeesToPaise } from "./format";

describe("formatPaise", () => {
  it("shows whole rupees without paise", () => {
    expect(formatPaise(250_000)).toBe("₹2,500");
    expect(formatPaise(100)).toBe("₹1");
  });

  it("shows paise only when there are some", () => {
    expect(formatPaise(123_456)).toBe("₹1,234.56");
    expect(formatPaise(1_250)).toBe("₹12.50");
  });

  it("groups digits the Indian way", () => {
    expect(formatPaise(10_000_000)).toBe("₹1,00,000");
  });
});

describe("rupeesToPaise", () => {
  it.each([
    ["2,500", 250_000],
    ["2500", 250_000],
    ["12.5", 1_250],
    ["12.50", 1_250],
    [" 100 ", 10_000],
    ["1,00,000", 10_000_000],
  ])("reads %s as %i paise", (input, paise) => {
    expect(rupeesToPaise(input)).toBe(paise);
  });

  it.each(["", "abc", "-1", "0", "1.234", "12.", ".5x", "1e3"])("refuses %j", (input) => {
    expect(rupeesToPaise(input)).toBeNull();
  });
});

describe("datetime-local conversion", () => {
  it("round-trips a whole-minute UTC instant", () => {
    const iso = "2027-01-15T13:00:00.000Z";
    expect(localInputToIso(isoToLocalInput(iso))).toBe(iso);
  });

  it("gives the input format the browser expects", () => {
    expect(isoToLocalInput("2027-01-15T13:00:00Z")).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  });

  it("sends UTC with a Z, never a local time without a zone", () => {
    expect(localInputToIso("2027-01-15T18:30")).toMatch(/Z$/);
  });
});
