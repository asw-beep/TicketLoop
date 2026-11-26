/**
 * Buying a ticket in a real browser: finding it, holding it (M7), and paying for it on fakepay's
 * own page (M8).
 */
import { expect, test } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { FAKEPAY_URL } from "./env";
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

/** A ticket on sale for ₹2,000, and the seller on its page. */
async function putOnSale(admin: Page, browser: Browser, eventName: string): Promise<Page> {
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
  return seller;
}

test("a buyer finds a ticket, reserves it, gives it up, and another buyer buys it", async ({
  page: admin,
  browser,
}) => {
  test.setTimeout(60_000);
  const eventName = `Monsoon Beats ${Date.now()}`;
  const seller = await putOnSale(admin, browser, eventName);
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

  // 5. Who pays for it, on the gateway's own page, and comes back to the order.
  await rival.getByRole("button", { name: "Checkout" }).click();
  await expect(rival).toHaveURL(new RegExp(`^${FAKEPAY_URL}/checkout/`));
  await expect(rival.getByText("₹2,000")).toBeVisible();
  await rival.getByRole("button", { name: "Pay", exact: false }).first().click();
  await expect(rival.getByRole("heading", { name: "Your order" })).toBeVisible();
  // The worker hands the ticket over (M9), and the page follows without a reload.
  await expect(rival.getByRole("heading", { name: "Status" })).toContainText("Completed", {
    timeout: 20_000,
  });
  await expect(rival.getByText("You've paid ₹2,000. The ticket is yours.")).toBeVisible();
  const orderUrl = rival.url();

  // It is on their lists, and nobody else's business.
  await rival.getByRole("link", { name: "My orders" }).click();
  const order = rival.getByRole("list", { name: "Your orders" }).getByRole("listitem");
  await expect(order).toContainText("Completed");
  await rival.getByRole("link", { name: "My tickets" }).click();
  await rival.getByRole("link", { name: eventName }).click();
  await expect(rival.getByRole("heading", { name: "Ticket", exact: true })).toBeVisible();
  // The seller's order number and proof of purchase are not the buyer's to see.
  await expect(rival.getByText("Order number")).toHaveCount(0);
  await expect(rival.getByRole("heading", { name: "Proof of purchase" })).toHaveCount(0);
  // ₹2,500 is the ticket's face value; this buyer paid ₹2,000 for it.
  await expect(rival.getByText("Price paid")).toHaveCount(0);
  await expect(rival.getByText("Face value", { exact: true })).toBeVisible();
  await expect(rival.getByText("At most ₹2,500, its face value.")).toBeVisible();
  await rival.getByRole("link", { name: "My holds" }).click();
  await expect(rival.getByRole("list", { name: "Your holds" }).getByRole("listitem")).toContainText(
    "Bought",
  );
  await buyer.goto(orderUrl);
  await expect(buyer.getByRole("heading", { name: "Not found" })).toBeVisible();

  // 6. The seller sees what happened, and not who.
  await seller.reload();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Sold");
  await expect(seller.getByRole("list", { name: "History" }).getByRole("listitem")).toHaveText([
    /^Listed as a draft by you/,
    /^Submitted for review by you/,
    /^Approved by an admin/,
    /^Reserved by a buyer/,
    /^Hold given up by a buyer/,
    /^Reserved by a buyer/,
    /^Sold; the ticket went to the buyer automatically/,
  ]);
  await expect(seller.getByText("Sam Varghese")).toHaveCount(0);
});

test("a declined payment lets the ticket go, and a late one is worth waiting for", async ({
  page: admin,
  browser,
}) => {
  test.setTimeout(60_000);
  const eventName = `Backwater Sessions ${Date.now()}`;
  const seller = await putOnSale(admin, browser, eventName);
  const listingUrl = seller.url();
  const buyer = await newPage(browser);
  await register(buyer, newEmail(), "Nila Das");
  await buyer.goto(listingUrl);
  await buyer.getByRole("button", { name: "Reserve" }).click();

  // 1. Two clicks' worth of Back: the hold, and the order, both lead to the same checkout.
  await buyer.getByRole("button", { name: "Checkout" }).click();
  await expect(buyer).toHaveURL(new RegExp(`^${FAKEPAY_URL}/checkout/`));
  const checkout = buyer.url();
  await buyer.goBack();
  await expect(buyer.getByText("You've started paying.")).toBeVisible();
  await expect(buyer.getByRole("button", { name: "Cancel hold" })).toHaveCount(0);
  await buyer.getByRole("link", { name: "Go to your order" }).click();
  await expect(buyer.getByRole("heading", { name: "Status" })).toContainText("Payment pending");
  await buyer.getByRole("button", { name: "Go to the payment page" }).click();
  await expect(buyer).toHaveURL(checkout);

  // 2. Declined: the order says so and the ticket is for sale again.
  await buyer.getByRole("button", { name: "Decline" }).click();
  await expect(buyer.getByText("The payment failed.")).toBeVisible();
  await buyer.getByRole("link", { name: "See if it is still for sale" }).click();
  await expect(buyer).toHaveURL(listingUrl);

  // 3. Again, and this time the gateway takes a few seconds to tell us. The page waits.
  await buyer.getByRole("button", { name: "Reserve" }).click();
  await buyer.getByRole("button", { name: "Checkout" }).click();
  await buyer.getByRole("button", { name: "Pay, webhook late" }).click();
  await expect(buyer.getByText("Waiting for the payment service")).toBeVisible();
  await expect(buyer.getByText("You've paid ₹2,000.")).toBeVisible({ timeout: 15_000 });

  await seller.reload();
  await expect(seller.getByRole("list", { name: "History" })).toContainText(
    "Payment failed; back on sale",
  );
});

test("checkout says when the payment service is down, and offers to try again", async ({
  page,
}) => {
  await register(page, newEmail());
  const id = "5f0c0000-0000-4000-8000-0000000000aa";
  const orderId = "5f0c0000-0000-4000-8000-0000000000dd";
  const ends = Date.now() + 500_000;
  // As the API does: once a try has reached the payment service, the hold shows as paying.
  let paying = false;
  await page.route(`**/api/v1/reservations/${id}`, (route) =>
    route.fulfill({
      json: paying
        ? { ...reservation(id, ends), status: "PAYMENT_PENDING", order_id: orderId }
        : reservation(id, ends),
    }),
  );
  const keys: (string | undefined)[] = [];
  await page.route("**/api/v1/orders", (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    return route.fulfill({ status: 201, json: { id: orderId } });
  });
  await page.route("**/api/v1/payments/intents", (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    paying = true;
    return route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      json: {
        type: "urn:ticketloop:problem:payment-gateway-unavailable",
        title: "The payment service is not answering",
        status: 503,
      },
    });
  });

  await page.goto(`/reservations/${id}`);
  await page.getByRole("button", { name: "Checkout" }).click();
  // The hold now shows as paying, and the reason the payment page didn't open stays said.
  await expect(page.getByText("You've started paying")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Your ticket is still held");
  await page.getByRole("button", { name: "Go to the payment page" }).click();
  await expect.poll(() => keys.length).toBe(3);
  await expect(page.getByRole("alert")).toContainText("Your ticket is still held");

  expect(keys[0]).toMatch(/^[0-9a-f]{32}$/);
  expect(new Set(keys).size).toBe(3); // the order's key, then one per press
});

/** A hold as the API shows it, ending at `expiresAt`. */
function reservation(id: string, expiresAt: number) {
  return {
    id,
    listing_id: "5f0c0000-0000-4000-8000-0000000000bb",
    status: "CREATED",
    created_at: new Date(expiresAt - 600_000).toISOString(),
    expires_at: new Date(expiresAt).toISOString(),
    order_id: null,
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
  };
}

test("a hold counts down by the server's clock and says when it has run out", async ({ page }) => {
  await register(page, newEmail());
  const id = "5f0c0000-0000-4000-8000-0000000000aa";
  // The server is an hour behind this browser, and the hold has four of its seconds left.
  const serverNow = Date.now() - 3_600_000;
  await page.route(`**/api/v1/reservations/${id}`, (route) =>
    route.fulfill({
      headers: { Date: new Date(serverNow).toUTCString() },
      json: reservation(id, serverNow + 4_000),
    }),
  );

  await page.goto(`/reservations/${id}`);
  await expect(page.getByRole("timer", { name: "Time left" })).toHaveText(/^0:0[1-5]$/);
  await expect(page.getByText("Your hold ran out.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel hold" })).toHaveCount(0);
});
