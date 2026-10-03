/**
 * D2 shared primitives in a real browser (on /specimen): keyboard flows, focus return,
 * layout at the review widths, and axe -- including with every overlay open.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { WIDTHS, expectNoHorizontalOverflow, jumpChord, settled } from "./helpers";

async function openSpecimen(page: Page) {
  await page.goto("/specimen");
  await expect(page.getByRole("heading", { level: 2, name: "Data table" })).toBeVisible();
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

test("Jump: ⌘K / Ctrl K opens it, arrows and Enter choose, Escape returns focus", async ({ page }) => {
  await openSpecimen(page);
  const opener = page.getByRole("button", { name: "Open Jump" });
  await opener.focus();
  await page.keyboard.press(await jumpChord(page));
  const input = page.getByRole("combobox", { name: "Jump to" });
  await expect(input).toBeFocused();
  await audit(page);

  await input.fill("kestrel");
  const options = page.getByRole("option");
  await expect(options).toHaveText(["Ask “kestrel”", "What did Kestrel agree to in the renewal?, Partial"]);
  await page.keyboard.press("ArrowDown");
  await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("jump-asked")).toHaveText("Would ask: “kestrel”");

  await opener.click();
  await expect(input).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(input).toBeHidden();
  await expect(opener).toBeFocused();
});

test("lattice: one Tab stop, arrow keys between cells, the related claim lights, Enter opens", async ({ page }) => {
  await openSpecimen(page);
  const lattice = page.getByRole("table", { name: "Which claims cite which exhibits" });
  const first = lattice.getByRole("button", { name: "Lead claim cites Exhibit 1, DR-112" });
  await first.focus();
  await expect(lattice.locator('button[tabindex="0"]')).toHaveCount(1);
  await page.keyboard.press("ArrowDown");
  await expect(lattice.getByRole("button", { name: "Claim 2 cites Exhibit 2, Acme kickoff notes" })).toBeFocused();
  // Focus lights the claim it belongs to, without hover.
  await expect(page.getByTestId("specimen-13").locator("li", { hasText: "At the June kickoff" })).toHaveClass(/claimLit/);
  await page.keyboard.press("ArrowDown");
  await expect(lattice.getByRole("button", { name: "Claim 3 cites Exhibit 3, HAL-4821" })).toBeFocused();
  // Down goes to the next row first, then the nearest cell in it.
  await page.keyboard.press("ArrowDown");
  await expect(lattice.getByRole("button", { name: "Claim 4 cites Exhibit 2, Acme kickoff notes" })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(lattice.getByRole("button", { name: "Claim 4 cites Exhibit 1, DR-112" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(lattice.getByRole("button", { name: "Claim 4 cites Exhibit 1, DR-112" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("lattice-gate")).toContainText("Opened exhibit 1 for Claim 4");
  await expect(page.getByTestId("lattice-gate")).toContainText("Shown for this answer: true");
  await expect(page.getByTestId("lattice-gate")).toContainText("would not show it: false");
});

test("filter tokens: open with the keyboard, choose, Done returns focus; the clear button is its own target", async ({ page }) => {
  await openSpecimen(page);
  const scope = page.getByRole("group", { name: "Search scope" });
  const sources = scope.getByRole("button", { name: "Sources: all my sources. Change" });
  await sources.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog").getByRole("checkbox", { name: "tickets", exact: true }).check();
  await audit(page);
  await page.getByRole("button", { name: "Done" }).click();
  await expect(scope.getByRole("button", { name: "Sources: tickets. Change" })).toBeFocused();
  await scope.getByRole("button", { name: "Remove sources filter" }).click();
  await expect(scope.getByRole("button", { name: "Sources: all my sources. Change" })).toBeVisible();
  await expect(scope.getByRole("button", { name: "Dates: any date, needs a fix. Change" })).toBeVisible();
});

test("menu, dialog and sheet: keyboard operable, focus trapped and returned", async ({ page }) => {
  await openSpecimen(page);
  const actions = page.getByRole("button", { name: "Actions for Employee handbook" });
  await actions.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem", { name: /Copy link/ })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Revoke grant" })).toBeFocused();
  await audit(page);
  await page.keyboard.press("Escape");
  await expect(actions).toBeFocused();

  const open = page.getByRole("button", { name: "Open dialog" });
  await open.click();
  const dialog = page.getByRole("dialog", { name: "Change Maya Okafor's access" });
  await expect(dialog).toBeVisible();
  await audit(page);
  for (let i = 0; i < 6; i += 1) await page.keyboard.press("Tab");
  expect(await dialog.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(open).toBeFocused();

  await page.getByRole("button", { name: "Open sheet" }).click();
  await expect(page.getByRole("dialog", { name: "Exhibit 1" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Open sheet" })).toBeFocused();
});

test("toast: announced, dismissible, and gone on its own", async ({ page }) => {
  await openSpecimen(page);
  await page.getByRole("button", { name: "Show a toast" }).click();
  const toast = page.getByText("Change applied", { exact: true });
  await expect(toast).toBeVisible();
  await audit(page);
  await expect(toast).toBeHidden({ timeout: 8000 });
});

test("the data table stacks into labelled blocks below 1024 px and never scrolls the page", async ({ page }) => {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await openSpecimen(page);
    await expectNoHorizontalOverflow(page);
    const table = page.getByRole("table", { name: "Connected sources (example)" });
    const header = table.getByRole("columnheader", { name: "Documents" });
    // Stacked: the header row is visually hidden but still names the cells.
    const hidden = await header.evaluate((h) => h.closest("thead")!.getBoundingClientRect().height <= 1);
    expect(hidden).toBe(width < 1024);
    await expect(table.getByRole("cell", { name: /access_unmappable:account/ })).toBeVisible();
  }
});

test("phones: the disagreement axis turns vertical and markers are 24 px targets", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await openSpecimen(page);
  const axis = page.getByRole("region", { name: /^Potential conflict\s*:\s*SCIM general availability date$/ });
  const [a, b] = await axis.getByRole("listitem").evaluateAll((items) => items.map((i) => i.getBoundingClientRect().top));
  expect(b!).toBeGreaterThan(a!);
  const marker = axis.getByRole("button", { name: "Citation 3: example" });
  const box = (await marker.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(24);
  expect(box.width).toBeGreaterThanOrEqual(24);
  await context.close();
});
