import { expect, type Page, type APIRequestContext } from "@playwright/test";
import { testids } from "../src/lib/testids";
export const ORIGIN = "http://127.0.0.1:3200";
export async function loginAs(page: Page, label: string) {
  await page.goto("/login");
  await page.getByTestId(testids.loginUser(label)).click();
  await expect(page).toHaveURL(/\/c$/, { timeout: 15_000 });
}
export async function switchTo(page: Page, label: string) {
  await page.getByTestId(testids.logout).click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  await loginAs(page, label);
}
export async function probe(request: APIRequestContext): Promise<{ slack_sent: number }> {
  const response = await request.get("http://127.0.0.1:8201/state");
  expect(response.ok()).toBe(true);
  return response.json();
}
