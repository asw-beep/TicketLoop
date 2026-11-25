/** Shared steps for the browser tests. */
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { PYTHON_ENV } from "./env";

export const PASSWORD = "correct horse battery staple";

export function newEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.in`;
}

export async function register(page: Page, email: string, name = "Asha Menon"): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Your pass" })).toBeVisible();
}

export async function logIn(page: Page, email: string, password: string): Promise<void> {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
}

/** Make an account an admin, the way an operator does: the script in scripts/make_admin.py. */
export function makeAdmin(email: string): void {
  execFileSync("uv", ["run", "--directory", "..", "python", "-m", "scripts.make_admin", email], {
    env: { ...process.env, ...PYTHON_ENV },
  });
}

/** A datetime-local value a month from now, so the event is always in the future. */
function nextMonth(): string {
  const d = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T19:30`;
}

export async function newPage(browser: Browser): Promise<Page> {
  return (await browser.newContext()).newPage();
}

export async function signInAsAdmin(page: Page): Promise<void> {
  const email = newEmail();
  await register(page, email, "Asha Admin");
  makeAdmin(email);
  await page.reload(); // the role is read again with the session
  await expect(page.getByRole("link", { name: "Verify tickets" })).toBeVisible();
}

export async function publishEvent(page: Page, name: string): Promise<void> {
  await page.getByRole("link", { name: "Events" }).click();
  await page.getByRole("button", { name: "New event" }).click();
  const form = page.getByRole("form", { name: "New event" });
  await form.getByLabel("Name").fill(name);
  await form.getByLabel("Venue").fill("Jawaharlal Nehru Stadium");
  await form.getByLabel("City").fill("Kochi");
  await form.getByLabel("Starts at").fill(nextMonth());
  await form.getByLabel("Organizer").fill("Backwater Live");
  await form.getByRole("button", { name: "Create event" }).click();
  const card = page.getByRole("listitem", { name });
  await expect(card.getByText("Draft")).toBeVisible();
  await card.getByRole("button", { name: "Publish" }).click();
  await expect(card.getByText("Published")).toBeVisible();
}

export async function registerTicket(page: Page, eventName: string): Promise<void> {
  await page.getByRole("link", { name: "My tickets" }).click();
  await page.getByRole("button", { name: "Register a ticket" }).click();
  const form = page.getByRole("form", { name: "Register a ticket" });
  const event = form.getByLabel("Event");
  const value = await event.locator("option", { hasText: eventName }).getAttribute("value");
  await event.selectOption(value!);
  await form.getByLabel("Ticket category").fill("GENERAL");
  await form.getByLabel("Seat or section").fill("Gallery C, row 12");
  await form.getByLabel("Price you paid (₹)").fill("2,500");
  const unique = Math.random().toString(36).slice(2, 10).toUpperCase();
  await form.getByLabel("Order or booking number").fill(`ORD-${unique}`);
  await form.getByLabel("Ticket number").fill(`TKT-${unique}`);
  await form.getByRole("button", { name: "Register ticket" }).click();
  await expect(page.getByRole("heading", { name: "Ticket", exact: true })).toBeVisible();
}

/** As a signed-in admin: verify the event's ticket from the queue, eligible for resale or not. */
export async function verifyTicket(
  page: Page,
  eventName: string,
  { eligible }: { eligible: boolean },
): Promise<void> {
  await page.getByRole("link", { name: "Verify tickets" }).click();
  const queued = page.getByRole("listitem", { name: eventName });
  await queued.getByLabel("Transfer eligible").setChecked(eligible);
  await queued.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText(`Verified: ${eventName},`)).toBeVisible();
}

/** As the seller on their verified ticket's page: offer it at `price` and send it for review. */
export async function listTicket(page: Page, price: string): Promise<void> {
  const form = page.getByRole("form", { name: "List this ticket" });
  await form.getByLabel("Asking price (₹)").fill(price);
  await form.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByRole("heading", { name: "Status" })).toContainText("Pending review");
}

/** As a signed-in admin: approve the event's listing from the review queue. */
export async function approveListing(page: Page, eventName: string): Promise<void> {
  await page.getByRole("link", { name: "Review listings" }).click();
  const queued = page.getByRole("listitem", { name: eventName });
  await queued.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(`Approved: ${eventName}.`)).toBeVisible();
}
