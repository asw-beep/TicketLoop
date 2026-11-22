/** Shared steps for the browser tests. */
import { execFileSync } from "node:child_process";
import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

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
  execFileSync("uv", ["run", "--directory", "..", "python", "-m", "scripts.make_admin", email]);
}
