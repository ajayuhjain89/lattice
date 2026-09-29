import { expect, test } from "@playwright/test";

import { ME_LENA, SOURCE } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

test("members see their own sources and counts, with nothing to change", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Sources" }).click();
  await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
  const table = page.getByRole("table", { name: "Sources you can read from" });
  await expect(table).toContainText("Halden Drive");
  await expect(table).toContainText("41");
  await expect(page.locator("main").getByRole("button")).toHaveCount(0);
});

test("administrators request a sync and read failures by opaque reference", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto("/sources");
  const table = page.getByRole("table", { name: "Connected sources and their sync status" });
  await expect(table.getByRole("button", { name: "Syncing…: Halden Drive" })).toBeDisabled();
  await table.getByRole("button", { name: "Sync now: Halden Slack" }).click();
  await expect.poll(() => log.syncs).toEqual([SOURCE.slack]);
  expect(log.headers.some((h) => h["x-lattice-request"] === "1")).toBe(true);

  await page.getByRole("button", { name: "Show records" }).click();
  await expect(page.getByText("rec_3f9a0c1d2e4b5a67")).toBeVisible();
  await expect(page.getByText(/External account ref: 360009913/)).toBeVisible();
});

for (const width of WIDTHS) {
  test(`sources fit at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { me: ME_LENA });
    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Connected sources" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}
