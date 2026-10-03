import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ME_LENA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, settled } from "./helpers";
import { mockApi } from "./mock-api";

/**
 * Development sign-in (this server runs with LATTICE_WEB_DEV_SIGN_IN=true and the Halden,
 * Veridian and long-name manifests). Production sign-in has its own server and suite:
 * sign-in-production.spec.ts.
 */
async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

const FAILED = { status: 401, body: { error: { code: "authentication_failed", message: "Sign-in failed.", request_id: "r" } } };

test("development sign-in is marked as such, grouped by organization, with seeded personas only", async ({ page }) => {
  await mockApi(page);
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in to Lattice" })).toBeVisible();
  await expect(page.getByText("Development sign-in.")).toBeVisible();
  await expect(page.getByText(/The API refuses it outside development and test/)).toBeVisible();
  const halden = page.getByRole("region", { name: "Halden" });
  await expect(halden.getByRole("button")).toHaveCount(8);
  await expect(halden.getByRole("button", { name: /Grace Whitfield/ })).toContainText("VP Customer Success");
  await expect(halden.getByText("Synthetic")).toBeVisible();
  await expect(page.getByRole("region", { name: "Veridian Analytics" }).getByRole("button")).toHaveCount(3);
  // The role label is all the seed says about a persona here: no clearance, accounts or groups.
  await expect(page.locator("main")).not.toContainText(/clearance|Confidential|Northpeak|Exec staff|administrator access/i);
  await audit(page);
});

test("a persona row sends that persona's exact seeded subject and organization, then opens Ask", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /Lena Brandt/ }).click();
  await expect(page).toHaveURL(/\/ask$/);
  expect(log.devLogins).toEqual([{ subject: "lena.brandt@halden.example", organization: "halden" }]);
  // Nothing about the sign-in is left in the address.
  expect(page.url()).not.toMatch(/lena|halden|subject|organization/i);
});

test("keyboard: Tab reaches the personas in order and Enter signs in", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto("/sign-in");
  await page.getByRole("link", { name: "What is Lattice?" }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: /Maya Okafor/ })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: /Daniel Reyes/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/ask$/);
  expect(log.devLogins).toEqual([{ subject: "daniel.reyes@halden.example", organization: "halden" }]);
});

test("focus stays on the chosen persona while signing in, and the wait is announced", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/v1/auth/dev-login", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify(FAILED.body) });
  });
  await page.goto("/sign-in");
  const priya = page.getByRole("button", { name: /Priya Natarajan/ });
  await priya.focus();
  await page.keyboard.press("Enter");
  await expect(priya).toHaveAttribute("aria-busy", "true");
  await expect(priya).toContainText("Signing in…");
  await expect(page.getByRole("status").filter({ hasText: "Signing in as Priya Natarajan…" })).toHaveCount(1);
  await expect(priya).toBeFocused();
  await expect(page.getByRole("alert").filter({ hasText: "Sign-in failed." })).toBeVisible();
  await expect(priya).toBeFocused();
});

test("an unknown or refused subject fails generically, once, and stays on the page", async ({ page }) => {
  const log = await mockApi(page, { devLogin: FAILED });
  await page.goto("/sign-in?next=%2Fsources");
  await page.getByRole("button", { name: /Li Wu/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(1);
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveText("Sign-in failed.");
  await expect(page).toHaveURL(/\/sign-in\?next=%2Fsources$/);
  expect(log.devLogins).toEqual([{ subject: "li.wu@northwind-logistik-oesterreich.example", organization: "longnames" }]);
  await audit(page);
});

test("a server without development sign-in says so", async ({ page }) => {
  await mockApi(page, { devLogin: { status: 404, body: { error: { code: "not_found", message: "Not found.", request_id: "r" } } } });
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /Maya Okafor/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveText("Development sign-in isn't available on this server.");
});

test("a sign-in limit is a wait, not a fault", async ({ page }) => {
  await mockApi(page, {
    devLogin: { status: 429, body: { error: { code: "rate_limited", message: "Too many requests. Wait a moment and try again.", request_id: "r" } }, headers: { "retry-after": "30" } },
  });
  await page.goto("/sign-in");
  await page.getByRole("button", { name: /Maya Okafor/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Too many requests." })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
});

test("next: the destination is named, and followed after sign-in", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto(`/sign-in?next=${encodeURIComponent("/access/audit")}`);
  await expect(page.getByTestId("continuation")).toHaveText("After signing in, you'll continue to Audit log/access/audit");
  await page.getByRole("button", { name: /Lena Brandt/ }).click();
  await expect(page).toHaveURL(/\/access\/audit$/);
});

for (const next of ["//evil.example/ask", "https://evil.example/", "/\t/evil.example", "/\\evil.example", "javascript:alert(1)", "/sign-in?next=/sources"]) {
  test(`next ${JSON.stringify(next)} is ignored: Ask, never another origin`, async ({ page }) => {
    await mockApi(page);
    await page.goto(`/sign-in?next=${encodeURIComponent(next)}`);
    await expect(page.getByTestId("continuation")).toHaveCount(0);
    await page.getByRole("button", { name: /Maya Okafor/ }).click();
    await expect(page).toHaveURL("http://127.0.0.1:3100/ask");
  });
}

test("the seeded e-mail form is a disclosure that works from the keyboard and keeps the e-mail out of the URL", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto("/sign-in");
  const summary = page.getByText("Use another seeded e-mail");
  await summary.focus();
  await page.keyboard.press("Enter");
  const email = page.getByRole("textbox", { name: "E-mail" });
  await expect(email).toBeVisible();
  await email.fill("tom.lindqvist@halden.example");
  await page.getByRole("textbox", { name: /Organization/ }).fill("halden");
  await email.press("Enter");
  await expect(page).toHaveURL("http://127.0.0.1:3100/ask");
  expect(log.devLogins).toEqual([{ subject: "tom.lindqvist@halden.example", organization: "halden" }]);
});

test("the identity provider's errors show on the development page too, from the closed set only", async ({ page }) => {
  await mockApi(page);
  await page.goto("/sign-in?error=sso_failed");
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveText(/^Sign-in didn't complete\./);
  await expect(page).toHaveTitle("Sign-in didn't complete · Sign in · Lattice");
  await page.goto(`/sign-in?error=${encodeURIComponent("<b>zq-injected</b>")}`);
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("zq-injected");
  await expect(page).toHaveTitle("Sign in · Lattice");
});

test("long organization, persona and e-mail names wrap or truncate without breaking the rows", async ({ page }) => {
  await mockApi(page);
  for (const width of [1440, 1280, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/sign-in");
    const org = page.getByRole("region", { name: /^Northwind Österreichische/ });
    await expect(org).toBeVisible();
    const row = org.getByRole("button", { name: /Maximiliane/ });
    const email = row.locator("[title]");
    await expect(email).toHaveAttribute("title", "maximiliane.oberhauser-wittgenstein.verwaltung@northwind-logistik-oesterreich.example");
    const box = await row.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    await expectNoHorizontalOverflow(page);
  }
});

test("the landing page and the sign-in page lead to each other", async ({ page }) => {
  await mockApi(page);
  await page.goto("/sign-in");
  await page.getByRole("link", { name: "What is Lattice?" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:3100/");
  await page.getByRole("banner").getByRole("link", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.getByRole("link", { name: "Lattice, home" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:3100/");
});
