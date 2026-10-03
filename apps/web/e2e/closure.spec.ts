import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ANSWERED, EV, FIXTURE_NOW, ID, ME_LENA, TRACE } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, settled } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/**
 * D7 closure sweep: every route, one structure. Each page has a "… · Lattice" title, one
 * main landmark and one h1, a skip link as the first stop, passes axe (WCAG 2.2 AA) at
 * rest, in forced colours and with reduced motion, and reflows at 320 px and at 200 %
 * zoom (640 CSS px) without page scroll. Page-specific behaviour lives in each surface's
 * own suite.
 */
interface Route {
  name: string;
  path: string;
  options?: MockOptions;
  title: RegExp;
  ready: (page: Page) => Promise<unknown>;
}

const h1 = (name: string | RegExp) => (page: Page) => page.getByRole("heading", { level: 1, name }).waitFor();

const ROUTES: Route[] = [
  { name: "landing", path: "/", title: /^Lattice — /, ready: h1("Answers you can follow back to the page.") },
  { name: "sign-in", path: "/sign-in?next=%2Fsources", title: /^Sign in · Lattice$/, ready: h1("Sign in to Lattice") },
  { name: "ask", path: "/ask", title: /^Ask · Lattice$/, ready: h1(/Ask what/) },
  { name: "evidence", path: `/evidence/${EV.decision}`, title: /^Evidence · Lattice$/, ready: h1(/DR-112/) },
  { name: "trace", path: `/traces/${TRACE.answered}`, title: /^Trace · Lattice$/, ready: h1(/When will SCIM ship/) },
  { name: "sources", path: "/sources", options: { me: ME_LENA }, title: /^Sources · Lattice$/, ready: (p) => p.getByRole("table").waitFor() },
  { name: "activity", path: "/activity", title: /^Activity · Lattice$/, ready: (p) => p.getByRole("link", { name: /When will SCIM ship/ }).first().waitFor() },
  { name: "organization runs", path: "/activity/organization", options: { me: ME_LENA }, title: /^Organization runs · Lattice$/, ready: (p) => p.getByRole("table").waitFor() },
  { name: "access", path: "/access", options: { me: ME_LENA }, title: /^Access · Lattice$/, ready: (p) => p.getByRole("table").waitFor() },
  { name: "member", path: `/access/members/${ID.maya}`, options: { me: ME_LENA }, title: /^Member access · Lattice$/, ready: h1("Maya Okafor") },
  { name: "audit", path: "/access/audit", options: { me: ME_LENA }, title: /^Audit log · Lattice$/, ready: (p) => p.getByTestId("audit-entry").first().waitFor() },
  { name: "providers", path: "/settings/providers", options: { me: ME_LENA }, title: /^AI providers · Lattice$/, ready: (p) => p.getByTestId("egress-lattice").waitFor() },
  { name: "not available", path: `/evidence/${EV.missing}`, title: /^Evidence · Lattice$/, ready: (p) => p.getByText("Not available").first().waitFor() },
  { name: "not found", path: "/no-such-page", title: /^Not found · Lattice$/, ready: h1("This page doesn't exist") },
];

async function open(page: Page, route: Route) {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  await mockApi(page, route.options);
  await page.goto(route.path);
  await route.ready(page);
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

for (const route of ROUTES) {
  test(`${route.name}: title, landmarks, skip link, axe in every mode, reflow`, async ({ page }) => {
    await open(page, route);
    await expect(page).toHaveTitle(route.title);
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    await audit(page);

    await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
    await page.reload();
    await route.ready(page);
    expect(await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running" && a.effect?.getComputedTiming().iterations !== Infinity).length)).toBe(0);
    await audit(page);
    await page.emulateMedia({ forcedColors: "none", reducedMotion: "no-preference" });

    for (const [width, height] of [
      [320, 640],
      [640, 450],
    ] as const) {
      await page.setViewportSize({ width, height });
      await page.reload();
      await route.ready(page);
      await expectNoHorizontalOverflow(page);
    }
  });
}

test("no focusable element is hidden: every Tab stop on a page is visible", async ({ page }) => {
  for (const route of ROUTES.filter((r) => ["ask", "sources", "member", "providers", "landing"].includes(r.name))) {
    await open(page, route);
    const hidden = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])")]
        .filter((el) => !el.closest("[inert], [aria-hidden='true']") && !(el as HTMLButtonElement).disabled)
        .filter((el) => el.getClientRects().length === 0 || getComputedStyle(el).visibility === "hidden")
        .filter((el) => !el.matches(".skip-link"))
        .map((el) => el.outerHTML.slice(0, 80)),
    );
    expect(hidden, route.name).toEqual([]);
  }
});

test("an answer's state, evidence and trace keep one status vocabulary", async ({ page }) => {
  await mockApi(page, { ask: () => ANSWERED });
  await page.goto("/activity");
  await expect(page.getByText("Answered").first()).toBeVisible();
  await page.goto("/ask");
  await expect(page.getByText("Answered").first()).toBeVisible(); // recent questions, sentence case
  const transform = await page.getByText("Answered").first().evaluate((el) => getComputedStyle(el).textTransform);
  expect(transform).toBe("none");
});
