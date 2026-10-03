/**
 * D5 operational surfaces together: Sources, Activity, Access (members, a member, the
 * audit log) and AI providers, under 200 % zoom, forced colours and reduced motion, and
 * by keyboard. Surface-specific behaviour lives in each surface's own spec.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { auditPages, FIXTURE_NOW, ID, ME_LENA, PROVIDERS_MANY, SOURCES_ADMIN_LONG, MANY_MEMBERS, MEMBER_MAYA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, settled } from "./helpers";
import { mockApi } from "./mock-api";

const PAGES: [string, (page: Page) => Promise<void>][] = [
  ["/sources", (p) => expect(p.getByTestId("register-summary")).toBeVisible()],
  ["/activity", (p) => expect(p.getByRole("heading", { name: /^Today/ })).toBeVisible()],
  ["/activity/organization", (p) => expect(p.getByRole("table", { name: "Organization runs (operational view)" })).not.toHaveAttribute("aria-busy")],
  ["/access", (p) => expect(p.getByRole("link", { name: "Member 02" })).toBeVisible()],
  [`/access/members/${ID.maya}`, (p) => expect(p.getByTestId("reach")).toBeVisible()],
  ["/access/audit", (p) => expect(p.getByTestId("audit-entry").first()).toBeVisible()],
  ["/settings/providers", (p) => expect(p.getByTestId("egress-lattice")).toBeVisible()],
];

async function open(page: Page) {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  await mockApi(page, {
    me: ME_LENA,
    adminSources: SOURCES_ADMIN_LONG,
    members: MANY_MEMBERS,
    member: { [ID.maya]: MEMBER_MAYA },
    audit: (b) => auditPages(b),
    providers: PROVIDERS_MANY,
  });
}

test("200 % zoom (640 CSS px): every operational page reflows without page scroll", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 450 });
  await open(page);
  for (const [path, ready] of PAGES) {
    await page.goto(path);
    await ready(page);
    await expectNoHorizontalOverflow(page);
  }
});

test("axe passes on every operational page, at rest", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page);
  for (const [path, ready] of PAGES) {
    await page.goto(path);
    await ready(page);
    await settled(page);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(results.violations.map((v) => `${path}: ${v.id} ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
  }
});

// Axe's contrast check doesn't model forced colours (it flags system black-on-white), so
// this pass checks the signals that must survive: dashed boundaries, icons, focus.
test("forced colours: boundaries keep their dashed edge, states keep their icons, lattice cells stay distinct", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await open(page);
  await page.goto("/sources");
  await expect(page.getByTestId("register-summary")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Halden Wiki" }).locator("svg").first()).toBeVisible();
  await page.goto("/activity/organization");
  const band = page.getByText(/Questions, answers and evidence aren't visible/).locator("xpath=ancestor::div[1]");
  await expect(band).toHaveCSS("border-top-style", "dashed");
  await page.goto("/settings/providers");
  const lattice = page.getByRole("table", { name: "Which data levels each AI provider may receive" });
  const kept = lattice.getByRole("button", { name: /^Restricted data isn't sent to ollama/ });
  const sent = lattice.getByRole("button", { name: /^Internal data may be sent to ollama/ });
  await expect(kept).toHaveCSS("border-top-style", "dashed");
  await expect(sent).toHaveCSS("border-top-style", "solid");
  expect(await sent.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe(await kept.evaluate((el) => getComputedStyle(el).backgroundColor));
});

test("reduced motion: nothing on these pages animates", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page);
  for (const [path, ready] of PAGES) {
    await page.goto(path);
    await ready(page);
    const running = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length);
    expect(running, path).toBe(0);
  }
});

test("keyboard: every row action is reachable by Tab, in reading order", async ({ page }) => {
  await open(page);
  await page.goto("/sources");
  await expect(page.getByTestId("register-summary")).toBeVisible();
  const reached: string[] = [];
  for (let i = 0; i < 40 && reached.length < 30; i++) {
    await page.keyboard.press("Tab");
    reached.push((await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.textContent ?? "")).trim());
  }
  expect(reached).toContain("Show failed records: Halden Support (Zendesk)");
  expect(reached).toContain("Sync now: Halden Slack");
  expect(reached.indexOf("Show failed records: Halden Support (Zendesk)")).toBeLessThan(reached.indexOf("Sync now: Halden Support (Zendesk)"));

  await page.goto(`/access/members/${ID.maya}`);
  await expect(page.getByTestId("reach")).toBeVisible();
  await page.getByRole("button", { name: "Change access" }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Change Maya Okafor's access" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Change access" })).toBeFocused();
});
