/**
 * M5 in a real browser: a verified ticket is listed for resale, an admin approves the listing,
 * and only then can someone else see it. A draft stays the seller's own.
 */
import { expect, test } from "@playwright/test";
import {
  newEmail,
  newPage,
  publishEvent,
  register,
  registerTicket,
  signInAsAdmin,
  verifyTicket,
} from "./helpers";

test("a verified ticket is listed, approved by an admin, and seen by another user", async ({
  page,
  browser,
}) => {
  const eventName = `Harbour Lights ${Date.now()}`;
  await signInAsAdmin(page);
  await publishEvent(page, eventName);

  // 1. A user registers a ticket; the admin verifies it as transfer eligible.
  const seller = await newPage(browser);
  await register(seller, newEmail(), "Uma Thomas");
  await registerTicket(seller, eventName);
  const ticketUrl = seller.url();
  await verifyTicket(page, eventName, { eligible: true });

  // 2. The seller lists it below face value and sends it for review.
  await seller.reload();
  const form = seller.getByRole("form", { name: "List this ticket" });
  await form.getByLabel("Asking price (₹)").fill("2,000");
  await form.getByRole("button", { name: "Submit for review" }).click();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Pending review");
  const listingUrl = seller.url();

  // The admin sees the open listing from the ticket's page too.
  await page.goto(ticketUrl);
  const onTicket = page.getByRole("region", { name: /Listed/ });
  await expect(onTicket).toContainText("Pending review");
  await expect(onTicket.getByRole("link", { name: "View listing" })).toBeVisible();

  // 3. The admin approves it from Review listings.
  await page.getByRole("link", { name: "Review listings" }).click();
  await expect(page.getByRole("tab", { name: "Pending review" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const queued = page.getByRole("listitem", { name: eventName });
  await expect(queued).toContainText("₹2,000");
  await expect(queued).toContainText("₹2,500");
  await expect(
    queued.getByRole("link", { name: "View the ticket and its evidence" }),
  ).toBeVisible();
  await queued.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(`Approved: ${eventName}.`)).toBeVisible();
  await expect(queued).toHaveCount(0);

  await page.getByRole("tab", { name: "All listings" }).click();
  await page.getByLabel("Status").selectOption("ACTIVE");
  await expect(page.getByRole("listitem", { name: eventName })).toContainText("Active");

  // 4. The seller sees it on sale, and how it got there.
  await seller.goto(listingUrl);
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Active");
  await expect(seller.getByRole("list", { name: "History" }).getByRole("listitem")).toHaveText([
    /^Listed as a draft by you/,
    /^Submitted for review by you/,
    /^Approved by an admin/,
  ]);

  // 5. Someone else sees the listing, but not its history or the seller's controls.
  const buyer = await newPage(browser);
  await register(buyer, newEmail(), "Sam Varghese");
  await buyer.goto(listingUrl);
  await expect(buyer.getByText(eventName)).toBeVisible();
  await expect(buyer.getByText("₹2,000")).toBeVisible();
  await expect(buyer.getByRole("heading", { name: "History" })).toHaveCount(0);
  await expect(buyer.getByRole("button", { name: "Cancel listing" })).toHaveCount(0);

  // 6. A draft is the seller's alone: anyone else gets the not-found page.
  await registerTicket(seller, eventName);
  await verifyTicket(page, eventName, { eligible: true });
  await seller.reload();
  const second = seller.getByRole("form", { name: "List this ticket" });
  await second.getByLabel("Asking price (₹)").fill("1,800");
  await second.getByRole("button", { name: "Save draft" }).click();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Draft");
  await buyer.goto(seller.url());
  await expect(buyer.getByRole("heading", { name: "Not found" })).toBeVisible();
  await expect(buyer.getByText("₹1,800")).toHaveCount(0);
});

test("a rejected listing tells the seller why", async ({ page, browser }) => {
  const eventName = `Backwater Jazz ${Date.now()}`;
  await signInAsAdmin(page);
  await publishEvent(page, eventName);
  const seller = await newPage(browser);
  await register(seller, newEmail(), "Uma Thomas");
  await registerTicket(seller, eventName);
  await verifyTicket(page, eventName, { eligible: true });
  await seller.reload();
  await seller.getByRole("button", { name: "Submit for review" }).click();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Pending review");

  await page.getByRole("link", { name: "Review listings" }).click();
  const queued = page.getByRole("listitem", { name: eventName });
  await queued.getByRole("button", { name: "Reject" }).click();
  await queued.getByLabel("Reason").fill("The seat on the listing doesn't match the ticket.");
  await queued.getByRole("button", { name: "Reject listing" }).click();
  await expect(page.getByText(`Rejected: ${eventName}.`)).toBeVisible();
  await expect(queued).toHaveCount(0);

  await seller.reload();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Rejected");
  await expect(
    seller.getByText("Reason: The seat on the listing doesn't match the ticket."),
  ).toBeVisible();
});

test("the seller lowers the price of a listing and is told it was saved", async ({
  page,
  browser,
}) => {
  const eventName = `Temple Run ${Date.now()}`;
  await signInAsAdmin(page);
  await publishEvent(page, eventName);
  const seller = await newPage(browser);
  await register(seller, newEmail(), "Uma Thomas");
  await registerTicket(seller, eventName);
  await verifyTicket(page, eventName, { eligible: true });
  await seller.reload();
  await seller.getByLabel("Asking price (₹)").fill("2,000");
  await seller.getByRole("button", { name: "Submit for review" }).click();
  await expect(seller.getByRole("heading", { name: "Status" })).toContainText("Pending review");

  const form = seller.getByRole("form", { name: "Change price" });
  await form.getByLabel("Asking price (₹)").fill("1,950");
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(form.getByText("Saved.")).toBeVisible();
  await expect(form.getByLabel("Asking price (₹)")).toHaveValue("1950");
  await expect(seller.getByRole("list", { name: "History" })).toContainText(
    "Price changed from ₹2,000 to ₹1,950 by you",
  );
});

test("a non-admin sees the not-found page on Review listings", async ({ page }) => {
  await register(page, newEmail());
  await expect(page.getByRole("link", { name: "Review listings" })).toHaveCount(0);
  await page.goto("/admin/listings");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
});
