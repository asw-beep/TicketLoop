/**
 * The riso look's own promises, checked in a real browser: focus stays easy to see, nothing
 * spills past a 360 px phone in either theme, and the print effects behave.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";
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

/** How wide the page's content is: wider than the viewport means a sideways scroll. */
export async function pageWidth(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth);
}

async function outline(locator: Locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) };
  });
}

test("focus is easy to see", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  for (const name of ["Skip to content", "TicketLoop home"]) {
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toHaveAccessibleName(name);
    const { style, width } = await outline(focused);
    expect(style, name).not.toBe("none");
    expect(width, name).toBeGreaterThanOrEqual(3);
  }
  const email = page.getByLabel("Email");
  await email.focus();
  const { style, width } = await outline(email);
  expect(style).not.toBe("none");
  expect(width).toBeGreaterThanOrEqual(3);
});

for (const colorScheme of ["light", "dark"] as const) {
  test(`the signed-out pages fit a 360 px phone (${colorScheme})`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await page.emulateMedia({ colorScheme });
    for (const path of ["/", "/login", "/register", "/forgot-password", "/no-such-page"]) {
      await page.goto(path);
      await expect(page.getByRole("heading").first()).toBeVisible();
      expect(await pageWidth(page), path).toBeLessThanOrEqual(360);
    }
  });
}

test("the wordmark is printed in two inks", async ({ page }) => {
  await page.goto("/");
  const wordmark = page.getByRole("link", { name: "TicketLoop home" });
  const shadow = await wordmark.evaluate((element) => getComputedStyle(element).textShadow);
  expect(shadow).not.toBe("none");
});

async function motion(locator: Locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return { animation: style.animationName, transition: style.transitionDuration };
  });
}

test("the buying pages fit a phone and hold still when motion is reduced", async ({
  page: admin,
  browser,
}) => {
  test.setTimeout(90_000);
  const eventName = `Look ${Date.now()}`;
  await signInAsAdmin(admin);
  await publishEvent(admin, eventName);
  const seller = await newPage(browser);
  await register(seller, newEmail(), "Ravi Nair");
  await registerTicket(seller, eventName);
  await verifyTicket(admin, eventName, { eligible: true });
  await seller.reload();
  await listTicket(seller, "1,200");
  await approveListing(admin, eventName);

  // Wide, as a guest: a house poster up top, a poster on the event's card, inks settling.
  const guest = await newPage(browser);
  await guest.setViewportSize({ width: 1280, height: 900 });
  await guest.goto(`/?q=${encodeURIComponent(eventName)}`);
  const card = guest
    .getByRole("list", { name: "Tickets on sale" })
    .getByRole("listitem", { name: eventName });
  await expect(card).toBeVisible();
  await expect(guest.locator(".hero .poster")).toHaveCount(1);
  const poster = card.locator(".poster--card");
  await expect(poster).toHaveCount(1);
  expect(await poster.getAttribute("data-motif")).not.toBe("PLAIN");
  expect((await motion(poster.locator(".poster__ink--b"))).animation).toBe("ink-settle");

  const eventPath = await card
    .getByRole("link", { name: "All tickets for this event" })
    .getAttribute("href");
  const listingPath = await card.getByRole("link", { name: eventName }).getAttribute("href");

  // A 360 px phone, both themes: nothing spills sideways.
  await guest.setViewportSize({ width: 360, height: 800 });
  for (const colorScheme of ["light", "dark"] as const) {
    await guest.emulateMedia({ colorScheme });
    for (const path of ["/", eventPath!, listingPath!]) {
      await guest.goto(path);
      await expect(guest.locator(".poster").first()).toBeVisible();
      expect(await pageWidth(guest), `${path} (${colorScheme})`).toBeLessThanOrEqual(360);
    }
  }

  // Reduced motion: every top ink rests where it is.
  await guest.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await guest.goto(`/?q=${encodeURIComponent(eventName)}`);
  await expect(card).toBeVisible();
  const inks = await guest.locator(".poster__ink--b").all();
  expect(inks.length).toBeGreaterThanOrEqual(2);
  for (const ink of inks) {
    expect(await motion(ink)).toEqual({ animation: "none", transition: "0s" });
  }

  // The seller's own lists carry thumbnails. A ticket's event summary has no category, so it
  // draws plain; a listing's has one, so it matches the event's own poster.
  await guest.goto(eventPath!);
  const motif = await guest.locator(".feature .poster").getAttribute("data-motif");
  await seller.goto("/tickets");
  const myTicket = seller.getByRole("list", { name: "Your tickets" }).getByRole("listitem").first();
  await expect(myTicket.locator(".poster--thumb")).toHaveAttribute("data-motif", "PLAIN");
  await seller.goto("/listings");
  const myListing = seller.getByRole("listitem", { name: eventName });
  await expect(myListing.locator(".poster--thumb")).toHaveAttribute("data-motif", motif!);

  await seller.setViewportSize({ width: 360, height: 800 });
  for (const colorScheme of ["light", "dark"] as const) {
    await seller.emulateMedia({ colorScheme });
    for (const path of ["/tickets", "/listings"]) {
      await seller.goto(path);
      await expect(seller.locator(".poster--thumb").first()).toBeVisible();
      expect(await pageWidth(seller), `${path} (${colorScheme})`).toBeLessThanOrEqual(360);
    }
  }
});
