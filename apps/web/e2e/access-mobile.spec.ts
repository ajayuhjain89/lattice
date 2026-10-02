import { expect, test } from "@playwright/test";

import { ID, ME_LENA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow } from "./helpers";
import { mockApi } from "./mock-api";

test("on a phone, members are stacked rows and dialogs are bottom sheets", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await mockApi(page, { me: ME_LENA });
  await page.goto("/access");
  await expect(page.getByRole("list", { name: "Members and their access" })).toBeVisible();
  await expect(page.getByRole("table")).toBeHidden();
  await expectNoHorizontalOverflow(page);

  await page.goto(`/access/members/${ID.maya}`);
  await page.getByRole("button", { name: "Change access" }).tap();
  const dialog = page.getByRole("dialog", { name: "Change Maya Okafor's access" });
  await expect(dialog).toBeVisible();
  const viewport = page.viewportSize();
  // Measured once the sheet has risen into place.
  await expect.poll(async () => {
    const box = await dialog.boundingBox();
    return box && [Math.round(box.x), Math.round(box.y + box.height)];
  }).toEqual([0, viewport?.height]);
  await expectNoHorizontalOverflow(page);
});
