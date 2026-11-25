/**
 * Buying a ticket in a real browser. M7 covers finding, reserving and holding it.
 */
import { expect, test } from "@playwright/test";
import {
  PASSWORD,
  approveListing,
  fillRegistration,
  listTicket,
  logIn,
  newEmail,
  newPage,
  publishEvent,
  register,
  registerTicket,
  signInAsAdmin,
  verifyTicket,
} from "./helpers";

test("a buyer finds a ticket, reserves it, gives it up, and another buyer takes it", async ({
  page: admin,
  browser,
}) => {
  const eventName = `Monsoon Beats ${Date.now()}`;
  await signInAsAdmin(admin);
  await publishEvent(admin, eventName);
  const seller = await newPage(browser);
  await register(seller, newEmail(), "Uma Thomas");
  await registerTicket(seller, eventName);
  await verifyTicket(admin, eventName, { eligible: true });
  await seller.reload();
  await listTicket(seller, "2,000");
  await approveListing(admin, eventName);
  await seller.reload();
  await expect(seller.getByRole("button", { name: "Reserve" })).toHaveCount(0);

  // 1. A guest finds it and is asked to log in, then comes back to the same ticket.
  const buyer = await newPage(browser);
  const buyerEmail = newEmail();
  await register(buyer, buyerEmail, "Sam Varghese");
  await buyer.getByRole("button", { name: "Log out" }).click();
  await buyer.getByRole("link", { name: "Find tickets" }).click();
  await buyer.getByLabel("Event").fill(eventName);
  await buyer.getByRole("button", { name: "Search" }).click();
  await buyer
    .getByRole("list", { name: "Tickets on sale" })
    .getByRole("listitem")
    .getByRole("link", { name: eventName })
    .click();
  await expect(buyer.getByRole("heading", { name: "Buy this ticket" })).toBeVisible();
  const listingUrl = buyer.url();
  await buyer.getByRole("link", { name: "Log in to reserve" }).click();
  await logIn(buyer, buyerEmail, PASSWORD);
  await expect(buyer).toHaveURL(listingUrl);

  // Another buyer already has the page open.
  const rival = await newPage(browser);
  await rival.goto(listingUrl);
  await rival.getByRole("link", { name: "Log in to reserve" }).click();
  await rival.getByRole("link", { name: "Create one" }).click();
  await fillRegistration(rival, newEmail(), "Ravi Nair");
  await expect(rival).toHaveURL(listingUrl); // a new account comes back to it too
  await expect(rival.getByRole("button", { name: "Reserve" })).toBeVisible();

  // 2. Reserve: ten minutes on the clock, counting down.
  await buyer.getByRole("button", { name: "Reserve" }).click();
  await expect(buyer.getByRole("heading", { name: "Your hold" })).toBeVisible();
  const timer = buyer.getByRole("timer", { name: "Time left" });
  await expect(timer).toHaveText(/^(10:00|9:5\d)$/);
  await expect(timer).toHaveText(/^9:5[0-7]$/);
  const holdUrl = buyer.url();

  // 3. The other buyer is told, and the page survives a reload of the buyer's hold.
  await rival.getByRole("button", { name: "Reserve" }).click();
  await expect(rival.getByRole("alert")).toContainText("Someone else reserved it first");
  await rival.goto(holdUrl);
  await expect(rival.getByRole("heading", { name: "Not found" })).toBeVisible();
  await buyer.reload();
  await expect(timer).toHaveText(/^9:/);

  await buyer.getByRole("link", { name: "My holds" }).click();
  const row = buyer.getByRole("list", { name: "Your holds" }).getByRole("listitem");
  await expect(row).toContainText("On hold");
  await row.getByRole("link", { name: eventName }).click();

  // 4. The buyer gives it up, and the other one gets it.
  await buyer.getByRole("button", { name: "Cancel hold" }).click();
  await buyer.getByRole("button", { name: "Yes, give it up" }).click();
  await expect(buyer.getByText("You gave up this hold.")).toBeVisible();
  await rival.goto(listingUrl);
  await rival.getByRole("button", { name: "Reserve" }).click();
  await expect(rival.getByRole("heading", { name: "Your hold" })).toBeVisible();

  // 5. The seller sees what happened, and not who.
  await seller.reload();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Reserved");
  await expect(seller.getByRole("list", { name: "History" }).getByRole("listitem")).toHaveText([
    /^Listed as a draft by you/,
    /^Submitted for review by you/,
    /^Approved by an admin/,
    /^Reserved by a buyer/,
    /^Hold given up by a buyer/,
    /^Reserved by a buyer/,
  ]);
  await expect(seller.getByText("Sam Varghese")).toHaveCount(0);
});

test("a hold counts down by the server's clock and says when it has run out", async ({ page }) => {
  await register(page, newEmail());
  const id = "5f0c0000-0000-4000-8000-0000000000aa";
  // The server is an hour behind this browser, and the hold has four of its seconds left.
  const serverNow = Date.now() - 3_600_000;
  await page.route(`**/api/v1/reservations/${id}`, (route) =>
    route.fulfill({
      headers: { Date: new Date(serverNow).toUTCString() },
      json: {
        id,
        listing_id: "5f0c0000-0000-4000-8000-0000000000bb",
        status: "CREATED",
        created_at: new Date(serverNow - 596_000).toISOString(),
        expires_at: new Date(serverNow + 4_000).toISOString(),
        listing: {
          id: "5f0c0000-0000-4000-8000-0000000000bb",
          event: {
            id: "5f0c0000-0000-4000-8000-0000000000cc",
            name: "Clockwork Orchestra",
            category: "CONCERT",
            city: "Kochi",
            venue: "Arena",
            starts_at: "2027-01-15T13:00:00Z",
          },
          ticket_category: "GENERAL",
          seat_details: null,
          asking_price_paise: 200_000,
          original_price_paise: 250_000,
        },
      },
    }),
  );

  await page.goto(`/reservations/${id}`);
  await expect(page.getByRole("timer", { name: "Time left" })).toHaveText(/^0:0[1-5]$/);
  await expect(page.getByText("Your hold ran out.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel hold" })).toHaveCount(0);
});
