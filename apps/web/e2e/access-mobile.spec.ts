import { expect, test } from "@playwright/test";

import { FIXTURE_NOW, ID, ME_LENA, MEMBER_MAYA, MEMBER_RAE_REMOVED, PREVIEW_MIXED } from "../src/test/fixtures";

import { expectNoHorizontalOverflow } from "./helpers";
import { mockApi } from "./mock-api";

test("on a phone, members are stacked rows and the review is a bottom sheet that stays readable", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  await mockApi(page, { me: ME_LENA, preview: PREVIEW_MIXED });
  await page.goto("/access");
  const table = page.getByRole("table", { name: "Members and their access" });
  await expect(table.getByRole("link", { name: "Maya Okafor" })).toBeVisible();
  // Stacked: the column headers are kept for assistive tech, not shown.
  await expect(table.locator("thead")).toHaveCSS("clip-path", "inset(50%)");
  await expectNoHorizontalOverflow(page);

  await page.goto(`/access/members/${ID.maya}`);
  await page.getByRole("button", { name: "Change access" }).tap();
  const dialog = page.getByRole("dialog", { name: "Change Maya Okafor's access" });
  await expect(dialog).toBeVisible();
  const viewport = page.viewportSize();
  await expect.poll(async () => {
    const box = await dialog.boundingBox();
    return box && [Math.round(box.x), Math.round(box.y + box.height)];
  }).toEqual([0, viewport?.height]);
  await dialog.getByRole("radio", { name: "Confidential" }).check();
  await dialog.getByRole("button", { name: "Review change" }).tap();
  const row = dialog.getByRole("row", { name: /Departments/ });
  await expect(row).toContainText("Loses");
  await expect(row).toContainText("Sales (removed)");
  // Stacked, each side is labelled on screen (the column headers stay for assistive tech).
  const labels = await row.locator("td").evaluateAll((cells) => cells.map((c) => getComputedStyle(c, "::before").content).filter((c) => c !== "none"));
  expect(labels).toEqual(['"Now"', '"After"']);
  await expectNoHorizontalOverflow(page);
});

test("on a phone, Add member and Reinstate are bottom sheets that fit, with targets of at least 24 px", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  const rae = MEMBER_RAE_REMOVED.member.id;
  await mockApi(page, { me: ME_LENA, member: { [ID.maya]: MEMBER_MAYA, [rae]: MEMBER_RAE_REMOVED } });
  await page.goto("/access");
  const add = page.getByRole("button", { name: "Add member" });
  await expect(add).toBeVisible();
  // WCAG 2.2 AA target size (2.5.8); the design system's standard control height.
  expect((await add.boundingBox())?.height).toBeGreaterThanOrEqual(24);
  await add.tap();
  const dialog = page.getByRole("dialog", { name: "Add a member" });
  await expect(dialog.getByRole("textbox", { name: "Identifier from your identity provider" })).toBeVisible();
  const viewport = page.viewportSize();
  await expect.poll(async () => {
    const box = await dialog.boundingBox();
    return box && [Math.round(box.x), Math.round(box.y + box.height)];
  }).toEqual([0, viewport?.height]);
  await dialog.getByRole("button", { name: "Add member" }).tap();
  await expect(dialog.getByText("Enter their name.")).toBeVisible();
  // The actions stay reachable at the bottom of the sheet.
  await expect(dialog.getByRole("button", { name: "Add member" })).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "Close" }).tap();

  await page.goto(`/access/members/${rae}`);
  await page.getByRole("button", { name: "Reinstate" }).tap();
  const sheet = page.getByRole("dialog", { name: "Reinstate Rae Lindqvist" });
  const row = sheet.getByRole("row", { name: /Clearance/ });
  await expect(row).toContainText("Loses");
  await expectNoHorizontalOverflow(page);
});
