import { expect, test } from "@playwright/test";

import { ACCOUNT_IDS, FIXTURE_NOW, ME_LENA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow } from "./helpers";
import { mockApi } from "./mock-api";

test("on a phone, accounts and references are stacked rows and the mapping review is a bottom sheet", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  await mockApi(page, { me: ME_LENA });
  await page.goto("/access/accounts");
  const table = page.getByRole("table", { name: "Accounts" });
  await expect(table.getByRole("link", { name: "Acme Corp" })).toBeVisible();
  await expect(table.locator("thead")).toHaveCSS("clip-path", "inset(50%)");
  const create = page.getByRole("button", { name: "Create account" });
  expect((await create.boundingBox())?.height).toBeGreaterThanOrEqual(24);
  await expectNoHorizontalOverflow(page);

  await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
  await page.getByRole("button", { name: "Remove the mapping of 360001001" }).tap();
  const sheet = page.getByRole("dialog", { name: "Remove a reference's mapping" });
  const viewport = page.viewportSize();
  await expect.poll(async () => {
    const box = await sheet.boundingBox();
    return box && [Math.round(box.x), Math.round(box.y + box.height)];
  }).toEqual([0, viewport?.height]);
  await sheet.getByRole("button", { name: "Review change" }).tap();
  await expect(sheet.getByTestId("mapping-effect")).toContainText("stop being readable until the reference is mapped again");
  await expect(sheet.getByRole("button", { name: "Remove mapping" })).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "Close" }).tap();

  await page.goto("/access/accounts/references");
  await expect(page.getByRole("row", { name: /360009913/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
