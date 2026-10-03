import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ME_MAYA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, settled } from "./helpers";
import { mockApi } from "./mock-api";

/**
 * The public landing page (D6): static, no API, no session probe, synthetic example only.
 * Every test records requests to /api from the first navigation on; the page must make
 * none, signed in or not.
 */
async function openLanding(page: Page, { signedIn = false }: { signedIn?: boolean } = {}) {
  const api: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api")) api.push(`${r.method()} ${r.url()}`);
  });
  // Anything that did reach /api would be answered (as a signed-in or signed-out session),
  // so a request can't hide behind a failure.
  await mockApi(page, signedIn ? { me: ME_MAYA } : { me: "unauthenticated" });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Answers you can follow back to the page." })).toBeVisible();
  return api;
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

test("renders without a session and makes no API request", async ({ page }) => {
  const api = await openLanding(page);
  await page.getByRole("button", { name: /^Exhibit 3:/ }).click(); // the one interactive island
  await page.getByRole("link", { name: "Boundaries" }).first().click();
  await page.waitForTimeout(300);
  expect(api).toEqual([]);
});

test("a signed-in visitor gets the same public page, with no session probe and no redirect", async ({ page }) => {
  await page.context().addCookies([{ name: "lattice_session", value: "a-session", url: "http://127.0.0.1:3100" }]);
  const api = await openLanding(page, { signedIn: true });
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("banner").getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
  await page.waitForTimeout(300);
  expect(api).toEqual([]);
});

test("document metadata: title, description, noindex, no invented canonical or social card", async ({ page }) => {
  await openLanding(page);
  await expect(page).toHaveTitle("Lattice — answers you can follow back to the page");
  await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", /searching only what you are allowed to read/);
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, nofollow");
  await expect(page.locator('link[rel="canonical"]')).toHaveCount(0);
  await expect(page.locator('meta[property^="og:"], meta[name^="twitter:"]')).toHaveCount(0);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("structure: landmarks, one h1, sections in narrative order, named navigation", async ({ page }) => {
  await openLanding(page);
  await expect(page.getByRole("banner")).toHaveCount(1);
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("contentinfo")).toHaveCount(1);
  await expect(page.getByRole("navigation", { name: "On this page" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Footer" })).toBeVisible();
  const headings = await page.locator("h1, h2, h3").evaluateAll((els) => els.map((e) => `${e.tagName} ${(e.textContent ?? "").replace(/\s+/g, " ").trim()}`));
  expect(headings).toEqual([
    "H1 Answers you can follow back to the page.",
    "H2 It searches only what you can read",
    "H3 Same question, two readers",
    "H2 Every claim keeps its evidence",
    "H2 Disagreement stays on the page",
    expect.stringMatching(/^H3 Potential conflict.*Directory Sync availability$/),
    "H2 Boundaries are drawn, not hidden",
    "H2 Access you can explain",
    "H2 Sign in through your organization.",
  ]);
  // Every example is labelled synthetic; nothing invented about customers or compliance.
  await expect(page.getByText(/synthetic company Halden/)).toHaveCount(6);
  await expect(page.locator("body")).not.toContainText(/free trial|book a demo|contact sales|pricing|testimonial|trusted by|customers love|SOC 2|ISO 27001|HIPAA|GDPR compliant/i);
  // Decorative SVGs are hidden from assistive tech.
  const exposedSvgs = await page.locator("svg:not([aria-hidden='true'])").count();
  expect(exposedSvgs).toBe(0);
});

test("the CTA is Sign in, and the secondary action jumps to how an answer is made", async ({ page }) => {
  await openLanding(page);
  const hero = page.locator("section", { has: page.getByRole("heading", { level: 1 }) });
  await expect(hero.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
  await hero.getByRole("link", { name: "How an answer is made" }).click();
  await expect(page).toHaveURL(/#how$/);
  await expect(page.getByRole("heading", { name: "It searches only what you can read" })).toBeInViewport();
  await page.getByRole("banner").getByRole("link", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("the masthead's section links land on their sections, clear of the sticky masthead", async ({ page }) => {
  await openLanding(page);
  for (const [link, heading] of [
    ["How it works", "It searches only what you can read"],
    ["Boundaries", "Boundaries are drawn, not hidden"],
    ["For administrators", "Access you can explain"],
  ] as const) {
    await page.getByRole("navigation", { name: "On this page" }).getByRole("link", { name: link }).click();
    const box = await page.getByRole("heading", { level: 2, name: heading }).boundingBox();
    const masthead = await page.getByRole("banner").boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(masthead!.y + masthead!.height);
    expect(box!.y).toBeLessThan(400);
  }
});

test("keyboard: skip link first, then the masthead, the hero actions and the evidence markers", async ({ page }) => {
  await openLanding(page);
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Lattice, home" })).toBeFocused();
  for (const name of ["How it works", "Boundaries", "For administrators", "Sign in"]) {
    await page.keyboard.press("Tab");
    await expect(page.getByRole("banner").getByRole("link", { name })).toBeFocused();
  }
  await page.keyboard.press("Tab");
  await expect(page.locator("main").getByRole("link", { name: "Sign in" }).first()).toBeFocused();
  // Focus is visible: a real outline, not colour alone.
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle);
  expect(outline).toBe("solid");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "How an answer is made" })).toBeFocused();

  // The skip link moves to main content.
  await page.goto("/");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();

  // The evidence markers are buttons: Tab to one, Enter opens its passage.
  const marker = page.getByRole("button", { name: /^Exhibit 1:/ });
  await marker.focus();
  await page.keyboard.press("Enter");
  await expect(marker).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: "Exhibit 1" }).locator("mark")).toHaveText(/will be generally available/);
});

test("evidence: each claim's marker opens its verified passage, announced once", async ({ page }) => {
  await openLanding(page);
  const viewer = page.locator("#evidence-exhibit");
  // Rendered open on exhibit 2 before any interaction.
  await expect(viewer).toHaveAttribute("aria-label", "Exhibit 2");
  await expect(viewer.locator("mark")).toHaveText(/expected in October/);
  await expect(viewer.getByRole("list", { name: "Exhibit 2 provenance" })).toContainText("Quote verified");
  await page.getByRole("button", { name: /^Exhibit 3:/ }).click();
  await expect(viewer).toHaveAttribute("aria-label", "Exhibit 3");
  await expect(viewer.locator("mark")).toHaveText(/planned for Q3/);
  await expect(page.getByRole("status").filter({ hasText: "Showing exhibit 3" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: /^Exhibit 2:/ })).toHaveAttribute("aria-pressed", "false");
});

test("hatching marks only boundaries the reader is entitled to see", async ({ page }) => {
  await openLanding(page);
  // Grace's withheld exhibit says what it is and that she can read it; Maya's list has no slot.
  const grace = page.getByRole("region", { name: "Evidence found for Grace Whitfield" });
  const maya = page.getByRole("region", { name: "Evidence found for Maya Okafor" });
  await expect(grace).toContainText("Not sent to the AI model · AI data policy · you can read it");
  await expect(grace).toContainText("Acme Corp — executive escalation notes");
  await expect(maya).not.toContainText("escalation");
  await expect(maya.getByRole("listitem")).toHaveCount(3);
  // Every hatched cell of the egress lattice is named as "isn't sent".
  const table = page.getByRole("table", { name: /Which data levels each model may receive/ });
  await expect(table.getByText(/isn't sent to/)).toHaveCount(3);
  await expect(table.getByText(/may be sent to/)).toHaveCount(3);
});

test("axe: the whole page at rest and after opening an exhibit", async ({ page }) => {
  await openLanding(page);
  await audit(page);
  await page.getByRole("button", { name: /^Exhibit 1:/ }).click();
  await audit(page);
});

test("reduced motion: the passage appears without a sweep", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openLanding(page);
  await page.getByRole("button", { name: /^Exhibit 1:/ }).click();
  const running = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length);
  expect(running).toBe(0);
  const animation = await page.locator("#evidence-exhibit mark").evaluate((el) => getComputedStyle(el).animationName);
  expect(animation).toBe("none");
});

test("forced colours: hatches drop to their dashed edges and the page still audits", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await openLanding(page);
  const withheld = page.getByRole("region", { name: "Evidence found for Grace Whitfield" }).locator("[class*='edge']").first();
  const before = await withheld.evaluate((el) => getComputedStyle(el, "::before"));
  expect(before.backgroundImage).toBe("none");
  expect(before.borderRightStyle).toBe("dashed");
  await audit(page);
});

for (const width of [1440, 1280, 1024, 768, 390, 320]) {
  test(`no horizontal overflow at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openLanding(page);
    await expectNoHorizontalOverflow(page);
    // The hero keeps its headline above the specimen and the specimen at full width.
    const title = await page.getByRole("heading", { level: 1 }).boundingBox();
    const specimen = await page.getByRole("figure", { name: /a brief as Lattice renders one/ }).boundingBox();
    if (width >= 1024) expect(specimen!.x).toBeGreaterThan(title!.x + title!.width - 1);
    else {
      expect(specimen!.y).toBeGreaterThan(title!.y + title!.height);
      expect(specimen!.width).toBeGreaterThan(width - 2 * 32);
    }
  });
}

test("200 % zoom (640 CSS px): reflows without page scroll, text intact", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 450 });
  await openLanding(page);
  await expectNoHorizontalOverflow(page);
  await expect(page.getByRole("navigation", { name: "On this page" }).getByRole("link", { name: "For administrators" })).toBeVisible();
});

test("without JavaScript the story, the specimens and the CTA are all there", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:3100/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Answers you can follow back to the page.");
  await expect(page.locator("#evidence-exhibit mark")).toHaveText(/expected in October/);
  await expect(page.getByRole("table", { name: /Which data levels each model may receive/ })).toBeVisible();
  await expect(page.getByText("Lattice can't confirm which is current.").first()).toBeVisible();
  await expect(page.locator("main").getByRole("link", { name: "Sign in" }).first()).toHaveAttribute("href", "/sign-in");
  await context.close();
});

test("light: no third-party request, no extra font subset, every script signed", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  const response = await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  expect(requests.filter((u) => !u.startsWith("http://127.0.0.1:3100/"))).toEqual([]);
  expect(requests.filter((u) => u.includes("-latin-ext"))).toEqual([]);
  expect(response!.headers()["content-security-policy"]).toContain("'strict-dynamic'");
  expect(await page.evaluate(() => [...document.scripts].filter((s) => !s.nonce).length)).toBe(0);
});
