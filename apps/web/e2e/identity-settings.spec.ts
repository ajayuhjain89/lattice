import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_NOW, IDENTITY_BLOCKED, ME_LENA, ME_MAYA, REVEALED_TOKEN } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, hydrated, settled } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/**
 * Identity administration (Phase 12, P12.4) at /settings/identity: the OIDC connection
 * (test sign-in, check, client secret, client ID, issuer or claim, disable/enable) and the
 * SCIM tokens (create once, rotate, revoke), against the mocked API. Secrets are typed
 * into masked fields that start empty and leave the page with their dialogs; a token is
 * shown once.
 */

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

async function openIdentity(page: Page, options: MockOptions = {}) {
  const log = await mockApi(page, { me: ME_LENA, ...options });
  await page.goto("/settings/identity");
  await expect(page.getByTestId("connection-facts")).toBeVisible();
  return log;
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

async function click(page: Page, name: string | RegExp) {
  const button = page.getByRole("button", { name, exact: typeof name === "string" });
  await hydrated(button);
  await button.click();
}

test("Identity is an administration page: sign-in and provisioning as the API states them, no secret anywhere", async ({ page }) => {
  await openIdentity(page);
  await expect(page.getByRole("heading", { level: 1, name: "Identity" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: /primary|main/i }).first();
  await expect(nav.getByRole("link", { name: "Identity" })).toHaveAttribute("aria-current", "page");
  const signIn = page.getByRole("region", { name: "Sign-in" });
  await expect(signIn).toContainText("Sign-in through the identity provider works.");
  await expect(page.getByTestId("connection-facts")).toContainText("Set in Lattice, encrypted");
  await expect(page.getByRole("table", { name: "Provisioning tokens" }).getByRole("row", { name: /Token ending Qx7f/ })).toContainText("Live");
  await expect(page.getByText(/Request refused: the person exists as a member added by hand/)).toBeVisible();
  expect(await page.content()).not.toMatch(/vault:|lsc_|env:LATTICE/);
  await expectNoHorizontalOverflow(page);
});

test("members never reach the identity settings", async ({ page }) => {
  await mockApi(page, { me: ME_MAYA });
  await page.goto("/settings/identity");
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
});

test("a test sign-in comes back to the page with the result, and creates no session", async ({ page }) => {
  const log = await openIdentity(page);
  await click(page, "Test sign-in");
  await expect(page).toHaveURL(/\/settings\/identity\?test=/);
  const result = page.getByTestId("test-result");
  await expect(result).toContainText("Test sign-in matched your identity");
  await expect(result).toContainText("sub: sub-lena");
  expect(log.devLogins).toHaveLength(0);
  await click(page, "Dismiss");
  await expect(result).toBeHidden();
  await expect(page).toHaveURL(/\/settings\/identity$/);
});

test("the client secret is replaced through a masked field that starts empty and is gone afterwards", async ({ page }) => {
  const log = await openIdentity(page);
  await click(page, "Replace client secret");
  const dialog = page.getByRole("dialog", { name: "Replace the client secret" });
  const field = dialog.getByLabel(/New client secret/);
  await expect(field).toHaveAttribute("type", "password");
  await expect(field).toHaveValue("");
  await audit(page);
  await field.fill("rotated-secret-value");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Quarterly rotation");
  await dialog.getByRole("button", { name: "Replace secret" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("Client secret replaced");
  expect(log.identityWrites.at(-1)?.body).toMatchObject({ client_secret: "rotated-secret-value", reason: "Quarterly rotation" });
  expect(await page.content()).not.toContain("rotated-secret-value");
  await click(page, "Replace client secret");
  await expect(page.getByRole("dialog").getByLabel(/New client secret/)).toHaveValue("");
});

test("disabling previews the sessions that end, then offers to enable again", async ({ page }) => {
  await openIdentity(page);
  await click(page, "Disable sign-in");
  const dialog = page.getByRole("dialog", { name: "Disable sign-in" });
  await expect(dialog.getByTestId("status-effects")).toContainText("6 sessions end at once");
  await audit(page);
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Incident");
  await dialog.getByRole("button", { name: "Disable sign-in" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("Sign-in disabled");
  await expect(page.getByTestId("connection-facts")).toContainText("Disabled: nobody can sign in through it");
  await expect(page.getByRole("button", { name: "Enable sign-in" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Test sign-in" })).toBeHidden();
});

test("an issuer change waits for the administrator's own test sign-in with the new values", async ({ page }) => {
  await openIdentity(page);
  await click(page, "Change issuer or claim");
  const dialog = page.getByRole("dialog", { name: "Change the issuer or identifying claim" });
  await expect(dialog).toContainText("Moving to a different identity provider is not supported");
  await dialog.getByRole("textbox", { name: /Issuer/ }).fill("https://new.halden.example");
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByText("Needs your test sign-in with these values")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Apply change" })).toBeDisabled();
  await audit(page);
});

test("a token is created and shown once; closing the dialog drops it", async ({ page }) => {
  await openIdentity(page);
  await click(page, "Create token");
  const dialog = page.getByRole("dialog", { name: "Create a provisioning token" });
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Okta provisioning");
  await dialog.getByRole("button", { name: "Create token" }).click();
  const revealed = dialog.getByTestId("revealed-token");
  await expect(revealed.getByLabel("Provisioning token")).toHaveValue(REVEALED_TOKEN.revealed_token);
  await expect(revealed.getByLabel("Provisioning token")).toBeFocused();
  await audit(page);
  await dialog.getByRole("button", { name: "I've stored it" }).click();
  await expect(dialog).toBeHidden();
  expect(await page.content()).not.toContain(REVEALED_TOKEN.revealed_token);
  await expect(page.getByRole("table", { name: "Provisioning tokens" }).getByRole("row", { name: /Token ending Zk9w/ })).toBeVisible();
});

test("revoking previews the consequence and asks for a reason", async ({ page }) => {
  const log = await openIdentity(page);
  await click(page, "Revoke token ending Qx7f");
  const dialog = page.getByRole("dialog", { name: "Revoke a provisioning token" });
  await expect(dialog.getByTestId("revoke-effects")).toContainText("It's the last live token");
  await dialog.getByRole("button", { name: "Revoke token" }).click();
  await expect(dialog.getByText("Give a reason for this change.")).toBeVisible();
  await audit(page);
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Provider replaced");
  await dialog.getByRole("button", { name: "Revoke token" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("Token ending Qx7f revoked");
  expect(log.identityWrites.map((w) => w.path).at(-1)).toMatch(/\/revoke$/);
});

test("a blocked private issuer says who acts, and the check says why", async ({ page }) => {
  await openIdentity(page, { identitySettings: IDENTITY_BLOCKED, check: { result: "outbound_blocked", step: "issuer", checked_at: "2026-10-13T10:00:00Z" } });
  await expect(page.getByText("Waiting for your Lattice operator")).toBeVisible();
  await click(page, "Check connection");
  await expect(page.getByTestId("check-result")).toContainText("outbound policy refuses this address");
  await audit(page);
});

test("keyboard: every action is reachable, dialogs trap focus and return it to their opener", async ({ page }) => {
  await openIdentity(page);
  const opener = page.getByRole("button", { name: "Change client ID" });
  await hydrated(opener);
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Change the client ID" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(dialog.locator(":focus")).toHaveCount(1); // focus stays inside
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

for (const [label, width, height] of [
  ["tablet", 768, 1024],
  ["320 px", 320, 640],
  ["200 % zoom (640 CSS px)", 640, 450],
] as const) {
  test(`${label}: the identity page and its dialogs reflow without page scroll`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openIdentity(page);
    await expectNoHorizontalOverflow(page);
    await click(page, "Replace client secret");
    const dialog = page.getByRole("dialog", { name: "Replace the client secret" });
    await expect(dialog.getByLabel(/New client secret/)).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const box = await dialog.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 0.5).toBe(true);
    await page.keyboard.press("Escape");
    await click(page, "Create token");
    const token = page.getByRole("dialog", { name: "Create a provisioning token" });
    await token.getByRole("textbox", { name: "Reason" }).fill("Reflow");
    await token.getByRole("button", { name: "Create token" }).click();
    await expect(token.getByTestId("revealed-token")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("forced colours and reduced motion: the result and applied lines keep their edges, nothing animates", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await mockApi(page, { me: ME_LENA });
  await page.goto("/settings/identity?test=c0ffee00-0000-4000-8000-000000000001");
  const result = page.getByTestId("test-result");
  await expect(result).toBeVisible();
  await expect(result).toHaveCSS("border-left-style", "solid");
  await audit(page);
  expect(await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length)).toBe(0);
});
