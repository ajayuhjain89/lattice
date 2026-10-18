/** Organization on a phone (Pixel 7, touch): reached from More, rows stacked, no page scroll. */
import { expect, test } from "@playwright/test";

import { ME_LENA, SETUP_IN_USE } from "../src/test/fixtures";

import { expectNoHorizontalOverflow } from "./helpers";
import { mockApi } from "./mock-api";

test("administrators open Organization from More and read the stacked record", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await mockApi(page, { me: ME_LENA, setup: SETUP_IN_USE });
  await page.goto("/ask");
  await page.getByRole("button", { name: "More" }).click();
  await page.getByRole("dialog", { name: "More" }).getByRole("link", { name: "Organization", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Organization", level: 1 })).toBeVisible();
  const ai = page.getByTestId("setup-record").getByRole("listitem", { name: "AI policy", exact: true });
  await expect(ai).toContainText("Needs attention");
  const link = ai.getByRole("link", { name: "Open AI providers" });
  // Small controls keep their look and get a 44 px hit area on touch (design-system §7).
  const hit = await link.evaluate((el) => parseFloat(getComputedStyle(el, "::after").height));
  expect(hit).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalOverflow(page);
});
