/**
 * D1 foundations (design-direction §4): self-hosted fonts and their fallbacks, the
 * responsive type scale, both theme maps under axe, and the specimen route.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { CONTRAST_PAIRS } from "../src/styles/contrast-pairs";

import { expectNoHorizontalOverflow, openAsk, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

const FAMILIES = ["Lattice Record", "Lattice Interface", "Lattice Ledger"];

async function openSpecimen(page: Page) {
  await page.goto("/specimen");
  await expect(page.getByRole("heading", { level: 1, name: "Specimen" })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function computed(page: Page, selector: string) {
  return page.locator(selector).first().evaluate((el) => {
    const s = getComputedStyle(el);
    return { size: s.fontSize, line: s.lineHeight, family: s.fontFamily, weight: s.fontWeight };
  });
}

test("fonts are self-hosted, preloaded, immutable and never fetched from a third party", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  await openSpecimen(page);

  const fonts = requests.filter((u) => /\.woff2(\?|$)/.test(u));
  expect(fonts.length).toBeGreaterThan(0);
  for (const url of fonts) expect(new URL(url).pathname).toMatch(/^\/fonts\/[a-z-]+(?:-\d00)?\.[0-9a-f]{10}\.woff2$/);
  expect(requests.filter((u) => /fonts\.(googleapis|gstatic)\.com/.test(u))).toEqual([]);

  const ready = await page.evaluate((families) => families.map((f) => document.fonts.check(`16px "${f}"`)), FAMILIES);
  expect(ready).toEqual([true, true, true]);

  const preloads = await page.locator('link[rel="preload"][as="font"]').evaluateAll((links) => links.map((l) => l.getAttribute("href")));
  expect(preloads).toHaveLength(3);

  const response = await page.request.get(fonts[0]!);
  expect(response.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
  expect(response.headers()["content-type"]).toBe("font/woff2");
});

test("Latin Extended downloads only for a page that uses it", async ({ page }) => {
  const extended: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("-latin-ext")) extended.push(r.url());
  });
  await mockApi(page);
  await page.goto("/sign-in");
  await page.evaluate(() => document.fonts.ready);
  expect(extended).toEqual([]);

  await openSpecimen(page); // its type checks include Łukasz Żółć and Dvořák
  expect(extended.length).toBeGreaterThan(0);
});

test("without the web fonts, every voice falls back to its metric-matched face and the page holds", async ({ page }) => {
  await page.route("**/fonts/*.woff2", (route) => route.abort());
  await openSpecimen(page);
  const ready = await page.evaluate((families) => families.map((f) => document.fonts.check(`16px "${f}"`)), FAMILIES);
  expect(ready).toEqual([false, false, false]);
  expect((await computed(page, '[data-testid="role-prose"] p')).family).toMatch(/^"Lattice Record", "Lattice Record Fallback"/);
  await expectNoHorizontalOverflow(page);
  await expect(page.getByTestId("contrast-ledger")).toBeVisible();
});

test("the type scale: three voices, and the display and title steps down on phones", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openSpecimen(page);
  expect(await computed(page, '[data-testid="role-display"] p')).toMatchObject({ size: "44px", line: "48px", weight: "500" });
  expect((await computed(page, '[data-testid="role-display"] p')).family).toMatch(/^"Lattice Record"/);
  expect(await computed(page, '[data-testid="role-prose"] p')).toMatchObject({ size: "17px", line: "28px" });
  expect(await computed(page, '[data-testid="role-title"] p')).toMatchObject({ size: "26px", weight: "650" });
  expect((await computed(page, '[data-testid="role-body"] p')).family).toMatch(/^"Lattice Interface"/);
  expect((await computed(page, '[data-testid="role-meta"] p')).family).toMatch(/^"Lattice Ledger"/);

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await computed(page, '[data-testid="role-display"] p')).toMatchObject({ size: "34px", line: "40px" });
  expect(await computed(page, '[data-testid="role-title"] p')).toMatchObject({ size: "22px" });
  expect(await computed(page, '[data-testid="role-lead"] p')).toMatchObject({ size: "19px" });
  expect(await computed(page, '[data-testid="role-body"] p')).toMatchObject({ size: "14px" });
});

test("the specimen holds at every review width", async ({ page }) => {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await openSpecimen(page);
    await expectNoHorizontalOverflow(page);
  }
});

for (const theme of ["Light (shipped)", "Dark map"]) {
  test(`axe passes on the specimen in the ${theme} theme map, and every pairing is measured as passing`, async ({ page }) => {
    await openSpecimen(page);
    await page.getByRole("button", { name: theme }).click();
    const ledger = page.getByTestId("contrast-ledger");
    await expect(ledger.getByText("Fail", { exact: true })).toHaveCount(0);
    await expect(ledger.getByText("Pass", { exact: true })).toHaveCount(CONTRAST_PAIRS.length);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
}

test("the product ships the light map: paper ground, interface voice, no theme attribute", async ({ page }) => {
  await openAsk(page);
  const body = await page.evaluate(() => {
    const s = getComputedStyle(document.body);
    return { background: s.backgroundColor, family: s.fontFamily, theme: document.documentElement.getAttribute("data-theme") };
  });
  expect(body).toEqual({ background: "rgb(244, 243, 239)", family: expect.stringMatching(/^"Lattice Interface"/), theme: null });
});
