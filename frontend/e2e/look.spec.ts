/**
 * The riso look's own promises, checked in a real browser: focus stays easy to see, nothing
 * spills past a 360 px phone in either theme, and the print effects behave.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

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
