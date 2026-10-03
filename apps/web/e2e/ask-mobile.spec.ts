/** D4 on a phone (Pixel 7, 390 px): one column, inline markers, sheet inspector, vertical axis. */
import { expect, test } from "@playwright/test";

import { ANSWERED, CONFLICTS, MANY_TO_MANY } from "../src/test/fixtures";

import { askFor, expectNoHorizontalOverflow } from "./helpers";

test.skip(({ isMobile }) => !isMobile, "phone layout");

test("the brief is one column: markers follow the claim, the margin is given back", async ({ page }) => {
  await askFor(page, ANSWERED);
  const claim = page.locator("[data-claim='claim-0']");
  const text = await claim.locator("p").boundingBox();
  const marker = await claim.getByRole("button", { name: /^Citation 1:/ }).boundingBox();
  expect(marker!.x).toBeGreaterThan(text!.x);
  expect(Math.round(text!.x)).toBe(16);
  expect(marker!.width).toBeGreaterThanOrEqual(24);
  await expectNoHorizontalOverflow(page);
});

test("the lattice folds to a one-line summary that expands", async ({ page }) => {
  await askFor(page, MANY_TO_MANY);
  const summary = page.getByRole("button", { name: /Claims and exhibits/ });
  await expect(summary).toContainText("4 claims · 4 exhibits");
  await expect(page.getByRole("table")).toHaveCount(0);
  await summary.click();
  await expect(page.getByRole("table", { name: "Which claims cite which exhibits" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the disagreement axis turns vertical", async ({ page }) => {
  await askFor(page, CONFLICTS);
  const items = page.getByRole("region", { name: /Pilot group size/ }).getByRole("listitem");
  const a = await items.nth(0).boundingBox();
  const b = await items.nth(1).boundingBox();
  expect(b!.y).toBeGreaterThan(a!.y + a!.height - 1);
  expect(Math.abs(a!.x - b!.x)).toBeLessThanOrEqual(1);
  await expectNoHorizontalOverflow(page);
});

test("the inspector is a bottom sheet; closing it returns focus to the marker", async ({ page }) => {
  await askFor(page, MANY_TO_MANY);
  const marker = page.locator("[data-claim='claim-3'] [data-citation='1']");
  await marker.click();
  const sheet = page.getByRole("dialog", { name: "Evidence inspector" });
  await expect(sheet.getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await sheet.getByRole("button", { name: "Next evidence" }).click();
  await expect(sheet.getByRole("heading", { name: "Acme kickoff — call notes" })).toBeVisible();
  await sheet.getByRole("button", { name: "Close evidence" }).click();
  await expect(sheet).toBeHidden();
  await expect(marker).toBeFocused();
});

test("Show in answer from the sheet closes it and lands on the claim's marker", async ({ page }) => {
  await askFor(page, MANY_TO_MANY);
  await page.locator("[data-claim='claim-3'] [data-citation='1']").click();
  const sheet = page.getByRole("dialog", { name: "Evidence inspector" });
  await sheet.getByRole("button", { name: /^Show in answer: SCIM general availability moves to December/ }).click();
  await expect(sheet).toBeHidden();
  await expect(page.locator("[data-claim='claim-0'] [data-citation='1']")).toBeFocused();
});

test("320 px: everything reflows", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await askFor(page, CONFLICTS);
  await expectNoHorizontalOverflow(page);
});
