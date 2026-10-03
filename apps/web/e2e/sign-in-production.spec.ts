import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { expectNoHorizontalOverflow, settled } from "./helpers";

/**
 * Production sign-in, on the server with development sign-in off (the `production`
 * project, playwright.config.ts) -- persona manifests are configured there and must be
 * ignored. The page is server-rendered and calls no API.
 */
async function open(page: Page, query = "") {
  const api: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api")) api.push(r.url());
  });
  await page.route("**/api/**", (route) => route.fulfill({ status: 500, body: "{}" }));
  await page.goto(`/sign-in${query}`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in to Lattice" })).toBeVisible();
  return api;
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

test("points to the identity provider; no persona, organization, e-mail or provider control", async ({ page }) => {
  const api = await open(page);
  await expect(page.getByText("Lattice signs you in through your organization's identity provider.")).toBeVisible();
  await expect(page.getByRole("list", { name: "How signing in works" }).getByRole("listitem")).toHaveCount(3);
  await expect(page.getByRole("button")).toHaveCount(0);
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await expect(page.locator("form")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(/Development sign-in|persona|Halden|Veridian|halden\.example|Google|Microsoft|Okta|Entra/);
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, nofollow");
  await expect(page).toHaveTitle("Sign in · Lattice");
  await page.waitForTimeout(300);
  expect(api).toEqual([]);
  await audit(page);
});

test("without JavaScript the page is complete", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:3101/sign-in?error=sso_failed&next=%2Fsources");
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toContainText("Sign-in didn't complete.");
  await expect(page.getByRole("link", { name: "Continue to Sources" })).toHaveAttribute("href", "/sources");
  await context.close();
});

for (const [code, role, text, title] of [
  ["sso_failed", "alert", "Sign-in didn't complete. Try again from your identity provider. If it keeps failing, ask your administrator whether your account has access to Lattice.", "Sign-in didn't complete"],
  ["sso_unavailable", "alert", "Your identity provider couldn't be reached. Try again in a moment.", "Identity provider unavailable"],
  ["rate_limited", "status", "Too many sign-in attempts from this browser. Wait a minute and try again.", "Too many sign-in attempts"],
] as const) {
  test(`${code}: its fixed sentence, once, in the title too`, async ({ page }) => {
    await open(page, `?error=${code}`);
    await expect(page.getByRole(role).filter({ hasText: /\S/ })).toHaveCount(1);
    await expect(page.getByRole(role).filter({ hasText: /\S/ })).toHaveText(text);
    await expect(page).toHaveTitle(`${title} · Sign in · Lattice`);
    await audit(page);
  });
}

test("every failure the API folds into sso_failed reads the same: no user, organization or connection is revealed", async ({ page }) => {
  // Expired or reused attempts, unknown keys, inactive or suspended members and unknown
  // identities all arrive as the one code; extra parameters change nothing.
  await open(page, "?error=sso_failed");
  const reference = await page.locator("main").innerHTML();
  for (const extra of ["&organization=halden", "&email=maya.okafor%40halden.example", "&reason=suspended", "&connection=halden-login-key-0123456789abc", "&outcome=expired"]) {
    await page.goto(`/sign-in?error=sso_failed${extra}`);
    expect(await page.locator("main").innerHTML()).toBe(reference);
  }
  for (const unknown of ["inactive", "expired", "user_not_found", "organization_not_found", "toString", "%3Cscript%3Ealert(1)%3C/script%3E"]) {
    await page.goto(`/sign-in?error=${unknown}`);
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(/inactive|expired|user_not_found|organization_not_found|alert\(1\)/);
  }
});

test("next: the destination is named with its path, and the continue link stays on this origin", async ({ page }) => {
  await open(page, `?next=${encodeURIComponent("/access/members/40000000-0000-4000-8000-000000000001")}`);
  await expect(page.getByTestId("continuation")).toHaveText("Where you were: /access/members/40000000-0000-4000-8000-000000000001");
  await expect(page.getByRole("link", { name: "Continue to Access" })).toHaveAttribute("href", "/access/members/40000000-0000-4000-8000-000000000001");
  await expect(page.getByRole("listitem").filter({ hasText: "Continue to Access" })).toBeVisible();
  await audit(page);
});

for (const next of ["//evil.example", "https://evil.example/ask", "/\t/evil.example", "/\\evil.example", "/sign-in"]) {
  test(`next ${JSON.stringify(next)} is ignored`, async ({ page }) => {
    await open(page, `?next=${encodeURIComponent(next)}`);
    await expect(page.getByTestId("continuation")).toHaveCount(0);
    const hrefs = await page.locator("a").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).href));
    for (const href of hrefs) expect(new URL(href).origin).toBe("http://127.0.0.1:3101");
  });
}

test("keyboard: skip link, home, What is Lattice?, then the continue link; nothing traps focus", async ({ page }) => {
  await open(page, "?next=%2Fsources");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Lattice, home" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "What is Lattice?" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Continue to Sources" })).toBeFocused();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement === document.body || document.activeElement === null)).toBe(true);
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("one column, the instruction above the fold, targets of 44 px", async ({ page }) => {
    await open(page, "?error=sso_failed&next=%2Fsources");
    await expectNoHorizontalOverflow(page);
    const alert = await page.getByRole("alert").filter({ hasText: /\S/ }).boundingBox();
    expect(alert!.y + alert!.height).toBeLessThan(844);
    const back = await page.getByRole("link", { name: "What is Lattice?" }).boundingBox();
    expect(back!.height).toBeGreaterThanOrEqual(44);
    await audit(page);
  });

  test("320 px and 200 % zoom reflow", async ({ page }) => {
    for (const width of [320, 640]) {
      await page.setViewportSize({ width, height: 640 });
      await open(page, `?next=${encodeURIComponent("/access/members/40000000-0000-4000-8000-000000000001")}`);
      await expectNoHorizontalOverflow(page);
    }
  });
});
