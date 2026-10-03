import { expect, test } from "@playwright/test";

import { FIXTURE_NOW, ID, ME_LENA, PROVIDERS_MANY, SOURCES_ADMIN_LONG } from "../src/test/fixtures";

import { expectNoHorizontalOverflow } from "./helpers";
import { mockApi } from "./mock-api";

test.beforeEach(async ({ page, isMobile }) => {
  test.skip(!isMobile, "phone layout");
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  await mockApi(page, { me: ME_LENA, adminSources: SOURCES_ADMIN_LONG, providers: PROVIDERS_MANY });
});

test("Sources on a phone: stacked rows, and Sync now reachable with a 44 px target", async ({ page }) => {
  await page.goto("/sources");
  const sync = page.getByRole("button", { name: "Sync now: Halden Slack" });
  await sync.scrollIntoViewIfNeeded();
  await expect(sync).toBeInViewport();
  // Not under the tab bar.
  const box = await sync.boundingBox();
  const bar = await page.getByRole("navigation", { name: "Primary" }).boundingBox();
  expect(box && bar && box.y + box.height <= bar.y).toBe(true);
  const hit = await sync.evaluate((el) => {
    const after = getComputedStyle(el, "::after");
    return after.content === "none" ? el.getBoundingClientRect().height : parseFloat(after.height) || el.getBoundingClientRect().height;
  });
  expect(hit).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalOverflow(page);
});

test("a member's access on a phone: the summary, then each rule with its value and effect", async ({ page }) => {
  await page.goto(`/access/members/${ID.maya}`);
  await expect(page.getByTestId("access-summary")).toBeVisible();
  const reach = page.getByTestId("reach");
  await expect(reach.getByText("Clearance", { exact: true })).toBeVisible();
  await expect(reach.getByText("Also in private channels you aren't in")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("AI providers on a phone: the lattice scrolls in a named region with its levels kept in view", async ({ page }) => {
  await page.goto("/settings/providers");
  const region = page.getByRole("region", { name: "Which data levels each AI provider may receive" });
  await expect(region).toBeVisible();
  await region.evaluate((el) => (el.scrollLeft = el.scrollWidth));
  const header = page.getByRole("rowheader", { name: "Restricted data" });
  const r = await region.boundingBox();
  const h = await header.boundingBox();
  expect(h && r && h.x >= r.x - 1).toBe(true);
  await expectNoHorizontalOverflow(page);
});
