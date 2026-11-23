/**
 * M4 in a real browser: an admin publishes an event, a user registers a ticket for it with
 * proof of purchase stored in MinIO, and the admin verifies it. Nobody but the owner and
 * admins can reach a ticket, and admin screens are not there for anyone else.
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  PASSWORD,
  logIn,
  newEmail,
  newPage,
  publishEvent,
  register,
  registerTicket,
  signInAsAdmin,
} from "./helpers";

const EVIDENCE = "e2e/fixtures/evidence.png";

/** Click Open on the only evidence item and return the presigned URL its tab lands on. */
async function openEvidence(page: Page, scope = page.locator("body")): Promise<string> {
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    scope.getByRole("button", { name: "Open" }).click(),
  ]);
  await popup.waitForURL(/^http:\/\/localhost:9000\//);
  const url = popup.url();
  await popup.close();
  return url;
}

test("an admin publishes an event, a user registers a ticket with evidence, the admin verifies it", async ({
  page,
  browser,
}) => {
  const eventName = `Monsoon Beats ${Date.now()}`;
  await signInAsAdmin(page);
  await publishEvent(page, eventName);

  const user = await newPage(browser);
  await register(user, newEmail(), "Uma Thomas");
  await registerTicket(user, eventName);
  await expect(user.getByText("Pending review")).toBeVisible();
  await expect(user.getByText("₹2,500")).toBeVisible();

  await user.getByLabel("File").setInputFiles(EVIDENCE);
  await user.getByRole("button", { name: "Upload" }).click();
  await expect(user.getByText("Uploaded.")).toBeVisible();
  await expect(user.getByText("PNG image 1")).toBeVisible();

  // The file comes straight from MinIO through a short-lived signed link, as the real type.
  const userLink = await openEvidence(user);
  const file = await user.request.get(userLink);
  expect(file.status()).toBe(200);
  expect(file.headers()["content-type"]).toBe("image/png");

  await page.getByRole("link", { name: "Verify tickets" }).click();
  const queued = page.getByRole("listitem", { name: eventName });
  await expect(queued.getByText("Gallery C, row 12")).toBeVisible();
  const adminLink = await openEvidence(page, queued);
  expect(new URL(adminLink).pathname).toBe(new URL(userLink).pathname);
  await expect(queued.getByLabel("Transfer eligible")).toBeChecked();
  await queued.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText(`Verified: ${eventName}, GENERAL.`)).toBeVisible();
  await expect(queued).toHaveCount(0);

  await user.reload();
  await expect(user.getByText("Verified", { exact: true })).toBeVisible();
  await expect(user.getByText("It can be listed for resale.")).toBeVisible();
  // A decided ticket takes no more evidence.
  await expect(user.getByRole("button", { name: "Upload" })).toHaveCount(0);
});

test("another user's ticket address shows the not-found page", async ({ page, browser }) => {
  const eventName = `Private Show ${Date.now()}`;
  await signInAsAdmin(page);
  await publishEvent(page, eventName);

  const owner = await newPage(browser);
  await register(owner, newEmail(), "Uma Thomas");
  await registerTicket(owner, eventName);
  const ticketUrl = owner.url();

  const stranger = await newPage(browser);
  await register(stranger, newEmail(), "Sam Varghese");
  await stranger.goto(ticketUrl);
  await expect(stranger.getByRole("heading", { name: "Not found" })).toBeVisible();
  await expect(stranger.getByText(eventName)).toHaveCount(0);
});

test("a non-admin sees the not-found page on admin screens", async ({ page }) => {
  await register(page, newEmail());
  await expect(page.getByRole("link", { name: "Verify tickets" })).toHaveCount(0);
  for (const path of ["/admin/events", "/admin/tickets"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  }
});

test("logging out forgets the last user's tickets before the next user logs in", async ({
  page,
  browser,
}) => {
  const eventName = `Shared Laptop ${Date.now()}`;
  const admin = await newPage(browser);
  await signInAsAdmin(admin);
  await publishEvent(admin, eventName);

  const bobEmail = newEmail();
  await register(await newPage(browser), bobEmail, "Bob Mathew");

  // Alice and then Bob in one tab, without a reload: only the app's own cache sits between them.
  await register(page, newEmail(), "Alice Joseph");
  await registerTicket(page, eventName);
  await page.getByRole("link", { name: "My tickets" }).click();
  await expect(page.getByRole("link", { name: eventName })).toBeVisible();
  await page.getByRole("button", { name: "Log out" }).click();
  await logIn(page, bobEmail, PASSWORD);
  await expect(page.getByRole("heading", { name: "Your pass" })).toBeVisible();

  // Hold Bob's list back, so anything on screen meanwhile can only come from the cache.
  await page.route("**/api/v1/tickets/me*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });
  await page.getByRole("link", { name: "My tickets" }).click();
  await expect(page.getByRole("heading", { name: "My tickets" })).toBeVisible();
  expect(await page.getByText(eventName).count()).toBe(0);
  await expect(page.getByText("You haven't registered any tickets yet.")).toBeVisible();
});

test("the address /tickets/me is not a ticket, and My tickets still works after it", async ({
  page,
}) => {
  await register(page, newEmail());
  await page.goto("/tickets/me");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  await page.getByRole("link", { name: "My tickets" }).click();
  await expect(page.getByRole("heading", { name: "My tickets" })).toBeVisible();
  await expect(page.getByText("You haven't registered any tickets yet.")).toBeVisible();
});
