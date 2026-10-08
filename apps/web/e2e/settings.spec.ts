/**
 * Organization (/settings, Phase 12 P12.1): the setup record the API derives, the
 * organization's name, and nothing that pretends to configure what later checkpoints
 * build. Rest, loading, fault, refusal and the incomplete states; keyboard; every width.
 * The closure, operations and a11y suites add it to their sweeps (axe in every mode,
 * forced colours, reduced motion, 320 px and 200 % zoom).
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ME_LENA, ME_MAYA, SETUP_FRESH, SETUP_IN_USE } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, settled, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

const record = (page: Page) => page.getByTestId("setup-record");
const row = (page: Page, name: string) => record(page).getByRole("listitem", { name, exact: true });

async function axe(page: Page) {
  await settled(page);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

test("administrators reach the setup record from the navigation and read it as the API wrote it", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, setup: SETUP_IN_USE });
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Organization", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Organization", level: 1 })).toBeVisible();
  await expect(page).toHaveTitle(/^Organization · Lattice$/);
  await expect(record(page).getByRole("listitem")).toHaveCount(8);
  await expect(row(page, "AI policy")).toContainText("Needs attention");
  await expect(row(page, "AI policy")).toContainText("gemini · gemini-3.5-flash-lite");
  await expect(row(page, "Account mapping")).toContainText("2 unmapped references");
  await expect(row(page, "Identity provider")).toContainText("Complete");
  await row(page, "Members").getByRole("link", { name: "Review access" }).click();
  await expect(page.getByRole("heading", { name: "Access", level: 1 })).toBeVisible();
  await axe(page);
});

test("a fresh organization: truthful states, who acts next, and no controls for what isn't built", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, setup: SETUP_FRESH });
  await page.goto("/settings");
  await expect(row(page, "Identity provider")).toContainText("Required");
  await expect(row(page, "Identity provider")).toContainText("Your Lattice operator acts on this");
  await expect(row(page, "First answer")).toContainText("Blocked");
  await expect(row(page, "Provisioning (SCIM)")).toContainText("Optional");
  // Rename is the one control; the rest are links to pages that exist.
  await expect(page.locator("main").getByRole("button")).toHaveText(["Rename"]);
  await expect(page.locator("main input, main select, main textarea")).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText(/%|score/i);
  await axe(page);
});

test("rename by keyboard alone: a reason is required, the result stays on the page, focus returns", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA, setup: SETUP_FRESH });
  await page.goto("/settings");
  const rename = page.getByRole("button", { name: "Rename" });
  await rename.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Rename the organization" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(rename).toBeFocused();

  await page.keyboard.press("Enter");
  // Focus moves into the dialog (its Close button first, as every dialog); Tab reaches the name.
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Tab");
  const name = dialog.getByRole("textbox", { name: /Name/ });
  await expect(name).toBeFocused();
  await name.fill("Halden Group");
  await dialog.getByRole("button", { name: "Rename" }).press("Enter");
  await expect(dialog.getByText("Say why: the reason is recorded in the audit log.")).toBeVisible();
  expect(log.renames).toHaveLength(0);
  await dialog.getByRole("textbox", { name: /Reason/ }).fill("Rebrand");
  await axe(page);
  await dialog.getByRole("button", { name: "Rename" }).press("Enter");
  await expect(page.getByTestId("renamed")).toContainText("Renamed to Halden Group");
  expect(log.renames).toEqual([{ name: "Halden Group", reason: "Rebrand", version: SETUP_FRESH.organization.version }]);
  expect(log.headers.some((h) => h["x-lattice-request"] === "1")).toBe(true);
});

test("loading, fault and refusal states", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/v1/admin/setup", async (route) => {
    await held;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SETUP_FRESH) });
  });
  await page.goto("/settings");
  await expect(page.getByRole("status").filter({ hasText: "Loading the setup record" })).toBeAttached();
  release();
  await expect(record(page)).toBeVisible();

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockApi(page, { me: ME_LENA, setup: "fault" });
  await page.goto("/settings");
  await expect(page.getByText("Couldn't load the setup record")).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await axe(page);

  await mockApi(page, { me: ME_LENA, setup: "forbidden" });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: /You need administrator access/ })).toBeVisible();
});

test("members have no Organization destination and get the permission page", async ({ page }) => {
  await mockApi(page, { me: ME_MAYA, setup: "forbidden" });
  await page.goto("/ask");
  await expect(page.getByRole("link", { name: "Organization", exact: true })).toHaveCount(0);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: /You need administrator access/ })).toBeVisible();
});

test("every width: the record reflows, rows stack on phones, nothing scrolls the page", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, setup: SETUP_IN_USE });
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/settings");
    await expect(record(page)).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const ai = row(page, "AI policy");
    const heading = await ai.getByRole("heading").boundingBox();
    const state = await ai.getByText("Needs attention").boundingBox();
    expect(heading && state).toBeTruthy();
    if (width < 768) expect(state!.y).toBeGreaterThan(heading!.y);
    else expect(Math.abs(state!.y - heading!.y)).toBeLessThan(12);
  }
});

test("forced colours and reduced motion: states keep their words and icons", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await mockApi(page, { me: ME_LENA, setup: SETUP_IN_USE });
  await page.goto("/settings");
  for (const name of ["Organization", "AI policy", "Account mapping"]) {
    await expect(row(page, name).locator("svg").first()).toBeVisible();
  }
  await expect(row(page, "Account mapping")).toContainText("Needs attention");
});
