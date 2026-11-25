/**
 * M6 in a real browser: a guest finds a ticket on the front page, narrows the search, shares
 * the address, and reaches the listing through its event.
 */
import { expect, test } from "@playwright/test";
import {
  approveListing,
  listTicket,
  newEmail,
  newPage,
  publishEvent,
  register,
  registerTicket,
  signInAsAdmin,
  verifyTicket,
} from "./helpers";

test("a guest searches, filters, shares the search and opens a listing", async ({
  page: admin,
  browser,
}) => {
  const unique = Date.now();
  const eventName = `Riverside Jazz ${unique}`;
  await signInAsAdmin(admin);
  await publishEvent(admin, eventName);
  const seller = await newPage(browser);
  await register(seller, newEmail(), "Uma Thomas");
  for (const price of ["2,000", "1,500"]) {
    await registerTicket(seller, eventName);
    await verifyTicket(admin, eventName, { eligible: true });
    await seller.reload();
    await listTicket(seller, price);
    await approveListing(admin, eventName);
  }

  // No account needed. Cheapest first.
  const guest = await newPage(browser);
  await guest.goto("/");
  await expect(guest.getByRole("heading", { name: "Find tickets" })).toBeVisible();
  await guest.getByLabel("Event").fill(`riverside jazz ${unique}`);
  await guest.getByRole("button", { name: "Search" }).click();
  const results = guest.getByRole("list", { name: "Tickets on sale" }).getByRole("listitem");
  await expect(results).toHaveCount(2);
  await expect(results.first()).toContainText("₹1,500");

  await guest.getByLabel("Sort by").selectOption("-price");
  await guest.getByLabel("Lowest price (₹)").fill("1,800");
  await guest.getByRole("button", { name: "Search" }).click();
  await expect(results).toHaveCount(1);
  await expect(results.first()).toContainText("₹2,000");

  // The address is the search: opened somewhere else, it shows the same thing.
  const friend = await newPage(browser);
  await friend.goto(guest.url());
  await expect(friend.getByLabel("Lowest price (₹)")).toHaveValue("1,800");
  await expect(
    friend.getByRole("list", { name: "Tickets on sale" }).getByRole("listitem"),
  ).toHaveCount(1);

  await guest.getByRole("button", { name: "Clear filters" }).click();
  await expect(guest.getByLabel("Event")).toHaveValue("");
  await guest.goBack();
  await expect(guest.getByLabel("Lowest price (₹)")).toHaveValue("1,800");

  await guest.getByLabel("Lowest price (₹)").fill("5,000");
  await guest.getByRole("button", { name: "Search" }).click();
  await expect(guest.getByText("No tickets match. Try fewer filters.")).toBeVisible();
  await guest.goBack();

  // Through the event page to the listing.
  await guest.getByRole("link", { name: "All tickets for this event" }).click();
  await expect(guest.getByRole("heading", { name: "Event", exact: true })).toBeVisible();
  await expect(results).toHaveCount(2);
  await results.first().getByRole("link").click();
  await expect(guest.getByRole("heading", { name: "Listing", exact: true })).toBeVisible();
  await expect(guest.getByText("₹1,500")).toBeVisible();
});

test("searching keeps the keyboard where it was, and the same search adds no history", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByLabel("Event");
  await box.fill("no such event anywhere");
  await box.press("Enter");
  await expect(page).toHaveURL(/q=no\+such\+event/);
  await expect(page.getByText("No tickets match. Try fewer filters.")).toBeVisible();
  await expect(box).toBeFocused();

  const entries = await page.evaluate(() => history.length);
  await box.press("Enter");
  await box.press("Enter");
  expect(await page.evaluate(() => history.length)).toBe(entries);

  // Back is the one thing that refills the form from the address.
  await page.goBack();
  await expect(box).toHaveValue("");
});

test("Load more continues where the page ended", async ({ page }) => {
  // Two pages from the API, without seeding 21 listings through the screens.
  const listing = (n: number) => ({
    id: `5f0c0000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    event: {
      id: "5f0c0000-0000-4000-8000-00000000000e",
      name: `Event ${n}`,
      category: "CONCERT",
      city: "Kochi",
      venue: "Arena",
      starts_at: "2027-01-15T13:00:00Z",
    },
    ticket_category: "GENERAL",
    seat_details: null,
    asking_price_paise: 100_000 + n,
    original_price_paise: 200_000,
    status: "ACTIVE",
    created_at: "2026-11-25T10:00:00Z",
  });
  await page.route(/\/api\/v1\/listings\?/, (route) => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    return route.fulfill({
      json: cursor
        ? { items: [listing(21)], next_cursor: null }
        : { items: Array.from({ length: 20 }, (_, i) => listing(i + 1)), next_cursor: "page-2" },
    });
  });

  await page.goto("/?city=Kochi");
  const results = page.getByRole("list", { name: "Tickets on sale" }).getByRole("listitem");
  await expect(results).toHaveCount(20);
  await page.getByRole("button", { name: "Load more" }).click();
  await expect(results).toHaveCount(21);
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
});
