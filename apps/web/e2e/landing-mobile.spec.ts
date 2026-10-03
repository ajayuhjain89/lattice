import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow } from "./helpers";

test.beforeEach(({ isMobile }) => {
  test.skip(!isMobile, "phone layout");
});

test("phone: the section links take their own row, wrap, and are 44 px targets", async ({ page }) => {
  const api: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api")) api.push(r.url());
  });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "On this page" });
  const bar = await page.getByRole("link", { name: "Lattice, home" }).boundingBox();
  for (const name of ["How it works", "Boundaries", "For administrators"]) {
    const box = await nav.getByRole("link", { name }).boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(box!.y).toBeGreaterThan(bar!.y + bar!.height - 1);
  }
  const signIn = await page.getByRole("banner").getByRole("link", { name: "Sign in" }).boundingBox();
  expect(signIn!.y).toBeLessThan(bar!.y + bar!.height);
  await expectNoHorizontalOverflow(page);
  expect(api).toEqual([]);
});

test("phone: the hero leads with the headline and the CTA, then the full-width specimen", async ({ page }) => {
  await page.goto("/");
  const cta = await page.locator("main").getByRole("link", { name: "Sign in" }).first().boundingBox();
  expect(cta!.y + cta!.height).toBeLessThan(844);
  expect(cta!.height).toBeGreaterThanOrEqual(44);
  const specimen = await page.getByRole("figure", { name: /a brief as Lattice renders one/ }).boundingBox();
  expect(specimen!.width).toBeGreaterThanOrEqual(390 - 2 * 16 - 1);
});

test("phone: the answer path and the disagreement axis turn vertical", async ({ page }) => {
  await page.goto("/");
  const stops = page.getByRole("list", { name: "How an answer is made" }).getByRole("listitem");
  const first = await stops.nth(0).boundingBox();
  const second = await stops.nth(1).boundingBox();
  expect(second!.y).toBeGreaterThan(first!.y + first!.height - 1);
  const positions = page.locator("#disagreement ol li");
  const a = await positions.nth(0).boundingBox();
  const b = await positions.nth(1).boundingBox();
  expect(b!.y).toBeGreaterThan(a!.y + a!.height - 1);
});

test("320 px: reflows with no page scroll", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto("/");
  await expectNoHorizontalOverflow(page);
});
