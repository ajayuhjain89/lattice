import { expect, test } from "@playwright/test";

import { FIXTURE_NOW, ME_LENA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, hydrated } from "./helpers";
import { mockApi } from "./mock-api";

test("on a phone, Identity is in More, the facts stack and the dialogs are bottom sheets", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  await mockApi(page, { me: ME_LENA });
  await page.goto("/settings/identity");
  await expect(page.getByTestId("connection-facts")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  const tokens = page.getByRole("table", { name: "Provisioning tokens" });
  await expect(tokens.getByText("Token ending Qx7f")).toBeVisible();
  await expect(tokens.locator("thead")).toHaveCSS("clip-path", "inset(50%)");

  const disable = page.getByRole("button", { name: "Disable sign-in" });
  await hydrated(disable);
  await disable.tap();
  const sheet = page.getByRole("dialog", { name: "Disable sign-in" });
  const viewport = page.viewportSize();
  await expect
    .poll(async () => {
      const box = await sheet.boundingBox();
      return box && [Math.round(box.x), Math.round(box.y + box.height)];
    })
    .toEqual([0, viewport?.height]);
  await expect(sheet.getByTestId("status-effects")).toContainText("sessions end at once");
  await expect(sheet.getByRole("button", { name: "Disable sign-in" })).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "Close" }).tap();

  // The More sheet lists the destination.
  await page.getByRole("button", { name: "More" }).tap();
  await expect(page.getByRole("dialog").getByRole("link", { name: "Identity" })).toBeVisible();
});
