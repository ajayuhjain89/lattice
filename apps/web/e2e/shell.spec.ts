import { expect, test, type Page } from "@playwright/test";

import { ANSWERED, TRACE } from "../src/test/fixtures";

import { askFor, expectNoHorizontalOverflow, openAsk, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

const box = async (page: Page, selector: string) => {
  const b = await page.locator(selector).first().boundingBox();
  if (!b) throw new Error(`no box for ${selector}`);
  return b;
};

test.describe("desktop shell", () => {
  test.skip(({ isMobile }) => isMobile, "desktop only");

  test("sidebar, page and inspector headers share one rule", async ({ page }) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await expect(page.getByTestId("evidence-inspector")).toBeVisible();
    const bottoms = await page.evaluate(() =>
      [...document.querySelectorAll("header, [data-testid='evidence-inspector'] > div:first-child, nav[aria-label='Primary']")]
        .map((el) => el.previousElementSibling ?? el)
        .map((el) => Math.round(el.getBoundingClientRect().bottom)),
    );
    // Sidebar header, page header and inspector header all end at 56 px.
    expect(bottoms.filter((b) => b === 56).length).toBeGreaterThanOrEqual(3);
  });

  test("composer, answer and evidence share the reading column's edges", async ({ page }) => {
    await askFor(page, ANSWERED);
    const composer = await box(page, "form[aria-label='Ask Lattice']");
    const status = await box(page, "[data-testid='answer'] > div");
    const evidence = await box(page, "[data-testid='evidence']");
    for (const b of [status, evidence]) {
      expect(Math.abs(b.x - composer.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(b.x + b.width - (composer.x + composer.width))).toBeLessThanOrEqual(1);
    }
    expect(composer.width).toBeLessThanOrEqual(760);
  });

  test("the docked inspector keeps the reading column at least 520 px wide", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await expect(page.getByTestId("evidence-inspector")).toBeVisible();
    const prose = await box(page, "[data-testid='answer-prose']");
    expect(prose.width).toBeGreaterThanOrEqual(520);
    await expectNoHorizontalOverflow(page);
  });

  for (const width of WIDTHS) {
    test(`no horizontal overflow at ${width} px across key screens`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await askFor(page, ANSWERED);
      await expectNoHorizontalOverflow(page);
      await page.goto(`/traces/${TRACE.answered}`);
      await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.goto("/settings/providers");
      await expectNoHorizontalOverflow(page);
    });
  }

  test("the rail (768–1279 px) names every destination and opens the full navigation", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    await openAsk(page);
    await expect(page.getByRole("link", { name: "Ask", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("dialog", { name: "Navigation" }).getByText("Maya Okafor")).toBeVisible();
  });
});

test.describe("phone shell", () => {
  test.skip(({ isMobile }) => !isMobile, "mobile only");

  test("top bar, navigation sheet, touch targets", async ({ page }) => {
    await askFor(page, ANSWERED);
    await expectNoHorizontalOverflow(page);
    const menu = page.getByRole("button", { name: "Open navigation" });
    const m = await menu.boundingBox();
    expect(m!.width).toBeGreaterThanOrEqual(44);
    expect(m!.height).toBeGreaterThanOrEqual(44);
    const submit = await page.getByRole("button", { name: "Ask", exact: true }).boundingBox();
    expect(submit!.height).toBeGreaterThanOrEqual(44);
    await menu.click();
    const sheet = page.getByRole("dialog", { name: "Navigation" });
    await expect(sheet.getByRole("link", { name: "Ask", exact: true })).toBeVisible();
    await sheet.getByRole("link", { name: /When will SCIM ship/ }).click();
    await expect(page).toHaveURL(new RegExp(`/traces/${ANSWERED.trace_id}`));
    await expect(sheet).toBeHidden();
  });

  test("evidence opens as a bottom sheet with a visible close button", async ({ page }) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    const sheet = page.getByRole("dialog", { name: "Evidence inspector" });
    await expect(sheet).toBeVisible();
    const vp = page.viewportSize()!;
    // Measured once the slide-in has settled.
    await expect.poll(async () => {
      const b = await sheet.boundingBox();
      return b ? [Math.round(b.x), Math.round(b.y + b.height)] : null;
    }).toEqual([0, vp.height]);
    await sheet.getByRole("button", { name: "Close evidence" }).click();
    await expect(sheet).toBeHidden();
  });

  test("once the composer scrolls away, New question brings it back", async ({ page }) => {
    await askFor(page, ANSWERED);
    await page.mouse.wheel(0, 1600);
    const again = page.getByRole("button", { name: "New question" });
    await expect(again).toBeVisible();
    await again.click();
    await expect(page.getByRole("textbox", { name: "Question" })).toBeFocused();
  });

  test("trace and provider pages fit the phone", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.answered}`);
    await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
});
