import { expect, test } from "@playwright/test";

import { ME_LENA } from "../src/test/fixtures";

import { openAsk } from "./helpers";
import { mockApi } from "./mock-api";

test("administrators see the read-only provider settings", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "AI providers" }).click();
  await expect(page.getByRole("heading", { name: "AI providers", level: 1 })).toBeVisible();
  await expect(page.getByText("Read-only", { exact: true })).toBeVisible();
  await expect(page.getByRole("table")).toContainText("gemini-3.5-flash-lite");
  await expect(page.locator("main").getByRole("button")).toHaveCount(0);
  await expect(page.locator("main input, main select")).toHaveCount(0);
});

test("members have no admin navigation and get the permission page", async ({ page }) => {
  await openAsk(page, { providers: "forbidden" });
  await expect(page.getByRole("link", { name: "AI providers" })).toHaveCount(0);
  await page.goto("/settings/providers");
  await expect(page.getByRole("heading", { name: /You need administrator access/ })).toBeVisible();
});
