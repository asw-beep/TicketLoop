/**
 * M3 in a real browser: register, keep the session across a reload, log out and in, reset a
 * forgotten password from the emailed link, and log out everywhere on a password change.
 */
import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

const MAILPIT = process.env.MAILPIT_URL ?? "http://localhost:8025";
const PASSWORD = "correct horse battery staple";
const NEW_PASSWORD = "a brand new passphrase";

function newEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.in`;
}

async function register(page: Page, email: string, name = "Asha Menon"): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Your pass" })).toBeVisible();
}

async function logIn(page: Page, email: string, password: string): Promise<void> {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
}

/** The reset link from the newest email Mailpit caught for this address. */
async function resetLinkFor(request: APIRequestContext, email: string): Promise<string> {
  const search = async () =>
    (await request.get(`${MAILPIT}/api/v1/search`, { params: { query: `to:"${email}"` } })).json();
  await expect.poll(async () => (await search()).messages_count, { timeout: 10_000 }).toBe(1);
  const [summary] = (await search()).messages;
  const message = await (await request.get(`${MAILPIT}/api/v1/message/${summary.ID}`)).json();
  const link = String(message.Text).match(/http\S+\/reset-password#token=[\w-]+/)?.[0];
  expect(link, message.Text).toBeTruthy();
  return link!;
}

test("a new user registers, keeps the session across a reload, and logs out and in", async ({
  page,
}) => {
  const email = newEmail();
  await register(page, email);
  await expect(page.getByText(email)).toBeVisible();

  // The access token lived in memory and is gone; the HttpOnly cookie brings the session back.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Your pass" })).toBeVisible();
  const cookies = await page.context().cookies();
  const refresh = cookies.find((cookie) => cookie.name === "refresh_token");
  expect(refresh).toMatchObject({ httpOnly: true, sameSite: "Strict", path: "/api/v1/auth" });
  expect(await page.evaluate(() => document.cookie)).not.toContain("refresh_token");

  await page.getByLabel("Full name").fill("Meera Iyer");
  await page.getByRole("button", { name: "Save name" }).click();
  await expect(page.getByText("Name saved.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Meera" })).toBeVisible();

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByText("You're logged out.")).toBeVisible();
  await page.goto("/account");
  await expect(page).toHaveURL(/\/login$/);

  await logIn(page, email, PASSWORD);
  await expect(page.getByText("Meera Iyer")).toBeVisible();
});

test("a wrong password gets one message, whatever was wrong", async ({ page }) => {
  await page.goto("/login");
  await logIn(page, newEmail(), PASSWORD);

  await expect(page.getByRole("alert")).toHaveText("Wrong email or password");
});

test("a forgotten password is reset from the emailed link, which then stops working", async ({
  page,
  request,
}) => {
  const email = newEmail();
  await register(page, email);
  await page.getByRole("button", { name: "Log out" }).click();

  await page.getByRole("link", { name: "Forgot your password?" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("a reset link is on its way")).toBeVisible();

  const link = await resetLinkFor(request, email);
  await page.goto(link);
  await expect(page).toHaveURL(/\/reset-password$/); // the token is out of the address bar
  await page.getByLabel("New password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Save new password" }).click();

  await expect(page.getByText("Password changed. Log in with your new password.")).toBeVisible();
  await logIn(page, email, PASSWORD);
  await expect(page.getByRole("alert")).toHaveText("Wrong email or password");
  await logIn(page, email, NEW_PASSWORD);
  await expect(page.getByRole("heading", { name: "Your pass" })).toBeVisible();

  await page.goto(link);
  await page.getByLabel("New password").fill("yet another passphrase");
  await page.getByRole("button", { name: "Save new password" }).click();
  await expect(page.getByRole("alert")).toContainText("already used");
});

test("changing the password logs out every device", async ({ browser }) => {
  const email = newEmail();
  const laptop = await (await browser.newContext()).newPage();
  const phone = await (await browser.newContext()).newPage();
  await register(laptop, email);
  await phone.goto("/login");
  await logIn(phone, email, PASSWORD);
  await expect(phone.getByRole("heading", { name: "Your pass" })).toBeVisible();

  await laptop.getByLabel("Current password").fill(PASSWORD);
  await laptop.getByLabel("New password").fill(NEW_PASSWORD);
  await laptop.getByRole("button", { name: "Change password" }).click();
  await expect(laptop.getByText("Password changed.")).toBeVisible();

  await phone.reload(); // its refresh token was revoked with the rest
  await expect(phone).toHaveURL(/\/login$/);
});
