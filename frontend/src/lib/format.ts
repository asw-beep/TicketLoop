/**
 * Money and time, between what people type and what the API speaks (integer paise, UTC).
 */

const wholeRupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const withPaise = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
});

/** 250000 → "₹2,500"; 123456 → "₹1,234.56". Indian digit grouping: ₹1,00,000. */
export function formatPaise(paise: number): string {
  const rupees = paise / 100;
  return paise % 100 === 0 ? wholeRupees.format(rupees) : withPaise.format(rupees);
}

// Digits with optional commas, then up to two decimal places. No signs, no exponents.
const RUPEES = /^\d[\d,]*(\.\d{1,2})?$/;

/** What someone typed as rupees ("2,500", "12.5") in paise, or null if it is not a price. */
export function rupeesToPaise(input: string): number | null {
  const text = input.trim();
  if (!RUPEES.test(text)) return null;
  const [whole, fraction = ""] = text.replaceAll(",", "").split(".");
  const paise = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return paise > 0 ? paise : null;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** A UTC instant as the value of <input type="datetime-local">, in the browser's time zone. */
export function isoToLocalInput(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A datetime-local value (the browser's time zone) as a UTC ISO string with a Z. */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString();
}

const eventDate = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** "Fri, 15 Jan 2027, 6:30 pm" in the browser's time zone. */
export function formatWhen(iso: string): string {
  return eventDate.format(new Date(iso));
}
