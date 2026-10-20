import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ACCOUNT_ACME, ACCOUNT_IDS, FIXTURE_NOW, ME_LENA, ME_MAYA } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, hydrated, settled } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/**
 * Accounts and external-reference mappings (Phase 12, P12.3) under Access: the accounts
 * ledger, creating an account, an account's record (edit, archive, reactivate, its
 * references), and the unmapped references -- each change previewed as Now → After by
 * the (mocked) API, given a reason, applied at the next sync. No content, no suggestions,
 * channel IDs never names.
 */

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

async function openAccounts(page: Page, options: MockOptions = {}) {
  const log = await mockApi(page, { me: ME_LENA, ...options });
  await page.goto("/access/accounts");
  await expect(page.getByRole("table", { name: "Accounts" }).getByRole("link", { name: "Acme Corp" })).toBeVisible();
  return log;
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

async function click(page: Page, name: string | RegExp) {
  const button = page.getByRole("button", { name });
  await hydrated(button);
  await button.click();
}

test("Accounts is a view of Access: the ledger, lifecycle and the references waiting", async ({ page }) => {
  await openAccounts(page);
  const views = page.getByRole("navigation", { name: "Access" });
  await expect(views.getByRole("link", { name: "Accounts" })).toHaveAttribute("aria-current", "page");
  const table = page.getByRole("table", { name: "Accounts" });
  await expect(table.getByRole("row", { name: /Orbital Freight/ })).toContainText("Archived");
  await expect(page.getByTestId("unmapped-notice")).toContainText("1 reference from your sources isn't mapped to an account");
  await page.getByRole("searchbox", { name: "Find an account" }).fill("orbital");
  await expect(table.getByRole("link")).toHaveCount(1);
  await expectNoHorizontalOverflow(page);
});

test("members never reach account administration", async ({ page }) => {
  await mockApi(page, { me: ME_MAYA });
  await page.goto("/access/accounts");
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
});

test("create an account, then map the waiting reference to it -- previewed, with a reason", async ({ page }) => {
  const log = await openAccounts(page);
  await page.getByRole("link", { name: "Review unmapped references" }).click();
  const table = page.getByRole("table", { name: "References the latest syncs could not map to an account" });
  const row = table.getByRole("row", { name: /360009913/ });
  await expect(row).toContainText("Not mapped");
  await hydrated(row.getByRole("button", { name: "Map 360009913" }));
  await row.getByRole("button", { name: "Map 360009913" }).click();
  let dialog = page.getByRole("dialog", { name: "Map a reference to an account" });
  // No suggestion is offered: nothing is selected until the administrator picks.
  await expect(dialog.getByRole("radio", { name: /Acme Corp/ })).not.toBeChecked();
  await dialog.getByRole("button", { name: "Create a new account instead" }).click();
  const create = page.getByRole("dialog", { name: "Create an account" });
  await create.getByRole("textbox", { name: "Name", exact: true }).fill("Fernhill Studios");
  await expect(create.getByRole("textbox", { name: "Key" })).toHaveValue("fernhill-studios");
  await create.getByRole("textbox", { name: "Reason" }).fill("Signed up on 24 September");
  await create.getByRole("button", { name: "Create account" }).click();
  dialog = page.getByRole("dialog", { name: "Map a reference to an account" });
  await expect(dialog.getByText("Selected: Fernhill Studios").or(dialog.getByRole("radio", { name: /Fernhill Studios/, checked: true }))).toBeVisible();
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByTestId("now-after")).toContainText("Not mapped");
  await expect(dialog.getByTestId("now-after")).toContainText("Fernhill Studios");
  await expect(dialog.getByTestId("mapping-effect")).toContainText("The record waiting for Ticketing organization 360009913 is indexed under Fernhill Studios.");
  await expect(dialog.getByTestId("mapping-effect")).toContainText("Nothing indexed changes before that sync");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("New customer");
  await dialog.getByRole("button", { name: "Map reference" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("Ticketing organization ID 360009913 → Fernhill Studios");
  await expect(row).toContainText("Mapped — waiting for the next sync");
  await page.getByRole("button", { name: "Sync Halden Support (Zendesk) now" }).click();
  await expect(page.getByText("A sync of Halden Support (Zendesk) is queued.")).toBeVisible();
  expect(log.syncs).toHaveLength(1);
  expect(log.accountWrites.map((w) => w.path)).toEqual([
    "/v1/admin/access/accounts",
    "/v1/admin/access/account-references/preview",
    "/v1/admin/access/account-references",
  ]);
  expect(log.accountWrites[2]!.body).toMatchObject({ external_ref: "360009913", reason: "New customer" });
});

test("an account's record: edit, archive (kept, read-only) and reactivate", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
  await expect(page.getByRole("heading", { level: 1, name: "Acme Corp" })).toBeVisible();
  await expect(page.getByRole("table", { name: "References mapped to Acme Corp" }).getByRole("row")).toHaveCount(4);
  await click(page, "Edit");
  const edit = page.getByRole("dialog", { name: "Edit Acme Corp" });
  await edit.getByRole("textbox", { name: /Other names/ }).fill("Acme\nACME Corp\nAcme Inc");
  await edit.getByRole("textbox", { name: "Reason" }).fill("Common names");
  await edit.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("Acme Corp updated");
  await expect(page.getByText("Also Acme, ACME Corp, Acme Inc")).toBeVisible();

  await click(page, "Archive");
  const archive = page.getByRole("dialog", { name: "Archive Acme Corp" });
  await expect(archive.getByTestId("lifecycle-effects")).toContainText("Its 3 references keep working");
  await archive.getByRole("textbox", { name: "Reason" }).fill("Churned");
  await archive.getByRole("button", { name: "Archive" }).click();
  await expect(page.getByTestId("archived-notice")).toContainText("nothing was deleted or hidden");
  await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add a reference" })).toHaveCount(0);
  // Focus returns to the page's own control after the dialog closes.
  await click(page, "Reactivate");
  const reactivate = page.getByRole("dialog", { name: "Reactivate Acme Corp" });
  await reactivate.getByRole("textbox", { name: "Reason" }).fill("Renewed");
  await reactivate.getByRole("button", { name: "Reactivate" }).click();
  await expect(page.getByRole("button", { name: "Add a reference" })).toBeVisible();
  expect(log.accountWrites.map((w) => w.path)).toEqual([
    `/v1/admin/access/accounts/${ACCOUNT_IDS.acme}`,
    `/v1/admin/access/accounts/${ACCOUNT_IDS.acme}/archive`,
    `/v1/admin/access/accounts/${ACCOUNT_IDS.acme}/reactivate`,
  ]);
});

test("move a reference: Now → After, applied at the next sync; a stale mapping is a conflict", async ({ page }) => {
  const kestrel = {
    id: ACCOUNT_IDS.kestrel,
    key: "kestrel-bank",
    name: "Kestrel Bank",
    aliases: [],
    status: "active" as const,
    lifecycle: "active" as const,
    archived_at: null,
    references: 0,
    assigned_members: 1,
    version: "2026-10-12T09:00:00.000000Z",
    created_at: "2026-09-27T08:00:00Z",
    mappings: [],
  };
  let conflict = true;
  await mockApi(page, {
    me: ME_LENA,
    accountRecords: { [ACCOUNT_IDS.acme]: ACCOUNT_ACME, [ACCOUNT_IDS.kestrel]: kestrel },
    accountWrite: (path) => {
      if (path.endsWith("/remap") && conflict) {
        conflict = false;
        return { status: 409, body: { error: { code: "conflict", message: "Changed.", request_id: "r", details: [{ field: "mapping", issue: "mapping_changed" }] } } };
      }
      return undefined;
    },
  });
  await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
  await click(page, "Move 360001001 to another account");
  const dialog = page.getByRole("dialog", { name: "Move a reference to another account" });
  await dialog.getByRole("radio", { name: /Kestrel Bank/ }).check();
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByTestId("mapping-effect")).toContainText("move from Acme Corp to Kestrel Bank. Until then they stay with Acme Corp.");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Acquired");
  await dialog.getByRole("button", { name: "Move reference" }).click();
  await expect(dialog.getByRole("alert")).toContainText("This reference's mapping changed after you opened this.");
  await dialog.getByRole("button", { name: "Move reference" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("→ Kestrel Bank");
  await expect(page.getByRole("table", { name: "References mapped to Acme Corp" }).getByRole("row")).toHaveCount(3);
});

test("add a reference by its exact ID; channel references are IDs, never names", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
  await click(page, "Add a reference");
  const dialog = page.getByRole("dialog", { name: "Add a reference" });
  await dialog.getByRole("combobox", { name: "Source" }).selectOption({ label: "Halden Slack — Chat" });
  await dialog.getByRole("textbox", { name: "Channel ID" }).fill("C0SALES0001");
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByTestId("mapping-effect")).toContainText("become Acme Corp's");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Shared channel");
  await dialog.getByRole("button", { name: "Map reference" }).click();
  await expect(page.getByRole("table", { name: "References mapped to Acme Corp" })).toContainText("C0SALES0001");
  expect(log.accountWrites.at(-1)!.body).toMatchObject({ ref_kind: "chat_channel", external_ref: "C0SALES0001" });
  for (const name of ["#sales", "acct-acme", "ext-acme-halden"]) await expect(page.getByText(name, { exact: false })).toHaveCount(0);
});

test("keyboard only: create an account and focus returns to Create account on cancel", async ({ page }) => {
  await openAccounts(page);
  const create = page.getByRole("button", { name: "Create account" });
  await hydrated(create);
  await create.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Create an account" });
  await expect(dialog.getByRole("textbox", { name: "Name", exact: true })).toBeVisible();
  // Tab moves through the fields in reading order; the key follows the name.
  await dialog.getByRole("textbox", { name: "Name", exact: true }).focus();
  await page.keyboard.type("Quarry Lane Media");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: "Key" })).toBeFocused();
  await expect(dialog.getByRole("textbox", { name: "Key" })).toHaveValue("quarry-lane-media");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: /Other names/ })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("combobox", { name: "Business status" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: "Reason" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(create).toBeFocused();
});

test("axe passes on the accounts, an account, its dialogs and the unmapped references", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openAccounts(page);
  await audit(page);
  await click(page, "Create account");
  await page.getByRole("dialog", { name: "Create an account" }).getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Enter the account's name.")).toBeVisible();
  await audit(page);
  await page.keyboard.press("Escape");
  await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
  await expect(page.getByRole("heading", { level: 1, name: "Acme Corp" })).toBeVisible();
  await audit(page);
  await click(page, "Remove the mapping of C0EXTACME01");
  const dialog = page.getByRole("dialog", { name: "Remove a reference's mapping" });
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByText("These records stay unreadable until mapped again")).toBeVisible();
  await audit(page);
  await page.keyboard.press("Escape");
  await page.goto(`/access/accounts/${ACCOUNT_IDS.orbital}`);
  await expect(page.getByTestId("archived-notice")).toBeVisible();
  await audit(page);
  await page.goto("/access/accounts/references");
  await expect(page.getByRole("row", { name: /360009913/ })).toBeVisible();
  await audit(page);
});

for (const [label, width, height] of [
  ["tablet", 768, 1024],
  ["320 px", 320, 640],
  ["200 % zoom (640 CSS px)", 640, 450],
] as const) {
  test(`${label}: accounts, an account, the mapping review and references reflow without page scroll`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openAccounts(page);
    await expectNoHorizontalOverflow(page);
    await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
    await expect(page.getByRole("heading", { level: 1, name: "Acme Corp" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await click(page, "Move 360001001 to another account");
    const dialog = page.getByRole("dialog", { name: "Move a reference to another account" });
    await expect(dialog.getByTestId("reference-facts")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const box = await dialog.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 0.5).toBe(true);
    await page.keyboard.press("Escape");
    await page.goto("/access/accounts/references");
    await expect(page.getByRole("row", { name: /360009913/ })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("forced colours and reduced motion: the comparison and notes keep their edges, nothing animates", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await mockApi(page, { me: ME_LENA });
  await page.goto(`/access/accounts/${ACCOUNT_IDS.acme}`);
  await click(page, "Archive");
  const dialog = page.getByRole("dialog", { name: "Archive Acme Corp" });
  await expect(dialog.getByTestId("now-after")).toBeVisible();
  await expect(dialog.getByText("Nobody's access changes.").locator("xpath=..")).toHaveCSS("border-left-style", "solid");
  expect(await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length)).toBe(0);
});
