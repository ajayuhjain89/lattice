import { expect, test } from "@playwright/test";

import { ME_LENA, TRACE } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

test("members see their own questions, each opening its trace", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Activity" }).click();
  await expect(page.getByRole("heading", { name: "Activity", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Organization activity" })).toHaveCount(0);
  await page.getByRole("link", { name: /When will SCIM ship for Acme/ }).click();
  await expect(page).toHaveURL(new RegExp(`/traces/${TRACE.answered}$`));
  expect(log.traces).toContain(TRACE.answered);
});

test("administrators see who asked and how it ran, never what", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/activity");
  const table = page.getByRole("table", { name: "Organization activity (operational view)" });
  await expect(table).toContainText("Grace Whitfield");
  await expect(table).toContainText("gemini-3.5-flash-lite");
  await expect(page.getByText(/isn't visible to administrators/)).toBeVisible();
});

for (const width of WIDTHS) {
  test(`activity fits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { me: ME_LENA });
    await page.goto("/activity");
    await expect(page.getByRole("heading", { name: "Organization activity" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}
