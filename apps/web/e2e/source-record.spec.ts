import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  FIXTURE_NOW,
  ME_LENA,
  ME_MAYA,
  ORG_SOURCE,
  SOURCE,
  SOURCE_RECORD_DRIVE,
  SOURCE_RECORD_ESCALATIONS,
  SOURCE_RECORD_NEW,
  SOURCES_ADMIN,
  SOURCES_ADMIN_ORG,
  UPLOAD_REFUSED_LAYOUT,
} from "../src/test/fixtures";

import { expectNoHorizontalOverflow, hydrated, settled } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/*
 * Export sources operated in the product (Phase 12, P12.6): the register's Add source and
 * next actions, a source's record, the upload with real progress, the check, the mapping
 * hand-off to Access, sync and retry, pause and resume, and disconnect.
 */

const REGISTER = { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] };

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ") + " " + n.html.slice(0, 160) + " " + n.failureSummary).join(", ")}`)).toEqual([]);
}

async function openRecord(page: Page, id: string, options: MockOptions = {}) {
  const log = await mockApi(page, { me: ME_LENA, adminSources: REGISTER, ...options });
  await page.goto(`/sources/${id}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await hydrated(page.getByRole("heading", { level: 1 }));
  return log;
}

/** A zip-looking body of `size` bytes (the mock answers; nothing parses it). */
function exportFile(name: string, size: number) {
  const buffer = Buffer.alloc(size, 0x61);
  buffer.write("PK\u0003\u0004", 0, "latin1");
  return { name, mimeType: "application/zip", buffer };
}

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

test("the register adds a source and shows each row's next action", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA, adminSources: REGISTER });
  await page.goto("/sources");
  const table = page.getByRole("table", { name: "Connected sources and their sync status" });
  await expect(table).not.toHaveAttribute("aria-busy");
  // Where the next step is elsewhere, the cell links there; where a sync can run, Sync now.
  const handbook = table.getByRole("row").filter({ hasText: "Halden Handbook" });
  await expect(handbook.getByRole("link", { name: "Upload an export: Halden Handbook" })).toHaveAttribute("href", `/sources/${ORG_SOURCE.handbook}`);
  await expect(handbook.getByRole("button", { name: /Sync now/ })).toHaveCount(0);
  const escalations = table.getByRole("row").filter({ hasText: "Halden Escalations" });
  await expect(escalations.getByRole("button", { name: "Sync now: Halden Escalations" })).toBeVisible();
  await expect(escalations.getByRole("link", { name: "Map account references" })).toHaveAttribute("href", "/access/accounts/references");
  await expect(table.getByRole("link", { name: "Halden Escalations" })).toHaveAttribute("href", `/sources/${ORG_SOURCE.escalations}`);

  await page.getByRole("button", { name: "Add source" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a source" });
  // Export formats only, named as exports: no live integration is offered.
  await expect(dialog.getByRole("radio")).toHaveCount(3);
  await expect(dialog.getByRole("radio", { name: "Slack workspace export" })).toBeVisible();
  await expect(dialog).not.toContainText(/connect your|sign in with|OAuth/i);
  await dialog.getByRole("button", { name: "Add source" }).click();
  await expect(dialog.getByText("Enter a name for this source.")).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Name" })).toBeFocused();
  await dialog.getByRole("radio", { name: "Tickets — JSON Lines export" }).check();
  await expect(dialog.getByRole("checkbox", { name: "Every record must belong to an account" })).toBeChecked();
  await dialog.getByRole("textbox", { name: "Name" }).fill("Northpeak Escalations");
  await expect(dialog.getByRole("textbox", { name: "Key" })).toHaveValue("northpeak-escalations");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Escalation exports for the support team");
  await dialog.getByRole("button", { name: "Add source" }).click();
  await expect(page).toHaveURL(/\/sources\/5d2c1b0a-8888-/);
  await expect(page.getByRole("heading", { level: 1, name: "Northpeak Escalations" })).toBeVisible();
  await expect(page.getByTestId("next-step")).toContainText("Upload an export");
  const write = log.sourceWrites.find((w) => w.method === "POST")!;
  expect(write.body).toMatchObject({ connector_type: "ticketing", key: "northpeak-escalations", account_scoped: true, reason: "Escalation exports for the support team" });
  // Never a path, and nothing the backend wouldn't accept.
  expect(Object.keys(write.body as object).sort()).toEqual(
    ["account_scoped", "authority", "connector_type", "default_departments", "default_sensitivity", "key", "name", "reason"].sort(),
  );
});

test("a record shows the check, the references waiting for an account, and nothing of the content", async ({ page }) => {
  await openRecord(page, ORG_SOURCE.escalations);
  await expect(page.getByTestId("source-key")).toHaveText("escalations");
  await expect(page.getByText("Tickets — JSON Lines export").first()).toBeVisible();
  await expect(page.getByTestId("next-step")).toHaveAttribute("data-action", "map_references");
  await expect(page.getByTestId("next-step")).toContainText("One account reference has no account yet");
  await expect(page.getByTestId("next-step").getByRole("link", { name: "Map references in Access" })).toHaveAttribute("href", "/access/accounts/references");
  const check = page.getByTestId("check-result");
  await expect(check.getByText("Records read")).toBeVisible();
  await expect(check).toContainText("42");
  await expect(check).toContainText("access_unmappable:account");
  const references = page.getByRole("table", { name: "Account references in the current export and whether each has an account" });
  await expect(references.getByRole("row").filter({ hasText: "360009913" })).toContainText("No account: held back");
  await expect(references.getByRole("row").filter({ hasText: "360001002" })).toContainText("Mapped — applies at the next sync");
  await expect(page.getByTestId("current-import")).toContainText("1.8 MiB zip archive");
  await expect(page.getByTestId("upload-limits")).toHaveText("Up to 100 MiB per upload, 512 MiB once expanded, 20,000 entries and 32 MiB per file.");
  await expect(page.getByText("Managing this source gives you no access to its content.")).toBeVisible();
});

test("upload: real bytes-sent progress, then validating, then accepted and checked", async ({ page }) => {
  const log = await openRecord(page, ORG_SOURCE.handbook, { uploadDelay: 800 });
  await expect(page.getByTestId("no-import")).toBeVisible();
  await expect(page.getByTestId("export-structure")).toContainText("A zip archive of .md files");
  const input = page.getByLabel("Export file");
  await expect(input).toHaveAttribute("accept", ".zip,application/zip");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText("Choose an export file first.")).toBeVisible();
  await input.setInputFiles(exportFile("handbook-export.zip", 2 * 1024 * 1024));
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  // While it is in flight the control is busy and cancellable. (Byte counts and the
  // "validating" state come from XMLHttpRequest's upload events, which an intercepted
  // request doesn't emit: upload.test.ts and SourceScreen.test.tsx cover them.)
  await expect(page.getByRole("button", { name: "Cancel upload" })).toBeVisible();
  await expect(page.getByTestId("upload-status")).toHaveText(/Export accepted: 12 files kept, 2 ignored\. A check is queued\./);
  expect(log.uploads).toHaveLength(1);
  expect(log.uploads[0]!.bytes).toBe(2 * 1024 * 1024);
  expect(log.uploads[0]!.headers["x-lattice-request"]).toBe("1");
  // The file's name is never sent: the body is the file itself, no form fields.
  expect(log.uploads[0]!.headers["content-type"]).toBe("application/zip");
  await expect(page.getByTestId("job-state")).toContainText("Check queued");
});

test("a refused upload says why, and the current export is unchanged", async ({ page }) => {
  await openRecord(page, ORG_SOURCE.escalations, { upload: () => ({ status: 422, body: UPLOAD_REFUSED_LAYOUT }) });
  await page.getByLabel("Export file").setInputFiles(exportFile("not-an-export.zip", 4096));
  await page.getByRole("button", { name: "Upload a replacement" }).click();
  await expect(page.getByTestId("upload-status")).toHaveText("Not accepted: The archive doesn't have the structure this source type reads.");
  await expect(page.getByTestId("current-import")).toBeVisible();
});

test("settings: the server's preview, an explicit acknowledgement for a widening change, then applied", async ({ page }) => {
  const log = await openRecord(page, ORG_SOURCE.escalations);
  await page.getByRole("button", { name: "Edit settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit Halden Escalations" });
  await dialog.getByRole("checkbox", { name: "Every record must belong to an account" }).uncheck();
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Tickets without an organization are fine here");
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByRole("heading", { name: "Review the change" })).toBeFocused();
  await expect(dialog.getByTestId("now-after")).toContainText("More people can read");
  await expect(dialog.getByText("More people can read records after this")).toBeVisible();
  const apply = dialog.getByRole("button", { name: "Apply change" });
  await expect(apply).toHaveAttribute("aria-disabled", "true");
  await apply.click({ force: true });
  expect(log.sourceWrites.filter((w) => w.method === "PATCH")).toHaveLength(0);
  await dialog.getByRole("checkbox", { name: "I've reviewed who gains access, and this is intended" }).check();
  await apply.click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("applied-change")).toContainText("Halden Escalations updated");
  const patch = log.sourceWrites.find((w) => w.method === "PATCH")!;
  expect(patch.body).toMatchObject({ version: 3, account_scoped: false, acknowledge_widening: true });
  await expect(page.getByTestId("settings")).toContainText("Records belong to an account only when they name one");
});

test("a stale version is refused with a way back to the latest", async ({ page }) => {
  await openRecord(page, ORG_SOURCE.escalations, {
    sourceWrite: (method) =>
      method === "PATCH"
        ? { status: 409, body: { error: { code: "conflict", message: "Changed.", request_id: "r", details: [{ field: "version", issue: "source_changed" }] } } }
        : undefined,
  });
  await page.getByRole("button", { name: "Edit settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit Halden Escalations" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("Halden Escalations (EMEA)");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Rename");
  await dialog.getByRole("button", { name: "Review change" }).click();
  await dialog.getByRole("button", { name: "Apply change" }).click();
  await expect(dialog.getByText("Changed since you opened this")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close and review the latest" })).toBeVisible();
});

test("pause blocks syncing with a reason shown; resume allows it again without starting one", async ({ page }) => {
  const log = await openRecord(page, ORG_SOURCE.escalations);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Pause Halden Escalations" });
  await expect(dialog.getByTestId("lifecycle-effects")).toContainText("Its documents stay searchable");
  await dialog.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(dialog.getByText("Give a reason for this change.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Export source is being migrated");
  await dialog.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Paused", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("next-step")).toHaveAttribute("data-action", "resume");
  const sync = page.getByRole("button", { name: "Sync now" });
  await expect(sync).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByText("Paused: resume the source to sync it.")).toBeVisible();
  await sync.click({ force: true });
  expect(log.syncs).toHaveLength(0);
  await page.getByTestId("next-step").getByRole("button", { name: "Resume" }).click();
  const resume = page.getByRole("dialog", { name: "Resume Halden Escalations" });
  await expect(resume).toContainText("Resuming doesn't start a sync");
  await resume.getByRole("textbox", { name: "Reason" }).fill("Migration done");
  await resume.getByRole("button", { name: "Resume" }).click();
  await expect(resume).toBeHidden();
  await expect(page.getByRole("button", { name: "Sync now" })).not.toHaveAttribute("aria-disabled");
  expect(log.syncs).toHaveLength(0);
  expect(log.sourceWrites.map((w) => w.path)).toEqual(["/pause", "/resume"]);
});

test("sync now and retry use the existing sync request", async ({ page }) => {
  const log = await openRecord(page, ORG_SOURCE.escalations, {
    sourceRecords: {
      [ORG_SOURCE.escalations]: { ...SOURCE_RECORD_ESCALATIONS, next_action: "retry_sync", job: { ...SOURCE_RECORD_ESCALATIONS.job!, id: "7f2a1b0c-aaaa-4d3e-9f4a-1b2c3d4e5faa", kind: "source_sync", mode: "full", status: "failed", attempts: 3, error_code: "worker_failed", created_at: "2026-09-29T09:30:00Z", started_at: "2026-09-29T09:30:01Z", finished_at: "2026-09-29T09:30:09Z", sync_run_id: null, import_id: SOURCE_RECORD_ESCALATIONS.current_import!.id } },
    },
  });
  await expect(page.getByTestId("job-state")).toContainText("The latest requested sync failed · worker_failed after 3 attempts");
  await page.getByRole("button", { name: "Retry sync" }).click();
  await expect.poll(() => log.syncs).toEqual([ORG_SOURCE.escalations]);
});

test("disconnect: what it deletes, the typed key and a reason, then back to the register", async ({ page }) => {
  const log = await openRecord(page, ORG_SOURCE.escalations);
  await page.getByRole("button", { name: "Disconnect" }).click();
  const dialog = page.getByRole("dialog", { name: "Disconnect Halden Escalations" });
  const preview = dialog.getByTestId("disconnect-preview");
  await expect(preview).toContainText("39 documents stop being readable at once");
  await expect(preview).toContainText("1 account reference mapping of this source is removed");
  await expect(preview).toContainText("Accounts, members, other sources and the audit log.");
  await dialog.getByRole("textbox", { name: "Type escalations to confirm" }).fill("escalation");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Replaced by the new help desk");
  await dialog.getByRole("button", { name: "Disconnect and delete" }).click();
  await expect(dialog.getByText("Type escalations exactly to confirm.")).toBeVisible();
  expect(log.sourceWrites.filter((w) => w.path === "/disconnect")).toHaveLength(0);
  await dialog.getByRole("textbox", { name: "Type escalations to confirm" }).fill("escalations");
  await dialog.getByRole("button", { name: "Disconnect and delete" }).click();
  await expect(page).toHaveURL(/\/sources$/);
  expect(log.sourceWrites.find((w) => w.path === "/disconnect")!.body).toMatchObject({ version: 3, confirm_key: "escalations", reason: "Replaced by the new help desk" });
});

test("an operator-managed source: no upload or settings edit, the operator workflow named", async ({ page }) => {
  await openRecord(page, SOURCE.drive);
  await expect(page.getByTestId("operator-managed")).toContainText("lattice sync");
  await expect(page.getByRole("button", { name: "Edit settings" })).toHaveCount(0);
  await expect(page.getByLabel("Export file")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sync now" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  expect(SOURCE_RECORD_DRIVE.managed_by).toBe("operator");
});

test("members and unknown sources get the same not-available pages", async ({ page }) => {
  await mockApi(page, { me: ME_MAYA });
  await page.goto(`/sources/${ORG_SOURCE.escalations}`);
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
  await mockApi(page, { me: ME_LENA });
  await page.goto("/sources/5d2c1b0a-0000-4e3f-9a8b-000000000000");
  await expect(page.getByRole("heading", { name: "This source isn't available" })).toBeVisible();
  await page.goto("/sources/not-a-source");
  await expect(page.getByRole("heading", { name: "This source isn't available" })).toBeVisible();
});

test("axe passes on the register, a record, and each dialog", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockApi(page, { me: ME_LENA, adminSources: REGISTER });
  await page.goto("/sources");
  await expect(page.getByRole("table", { name: "Connected sources and their sync status" })).not.toHaveAttribute("aria-busy");
  await audit(page);
  await page.getByRole("button", { name: "Add source" }).click();
  await page.getByRole("dialog", { name: "Add a source" }).getByRole("button", { name: "Add source" }).click();
  await audit(page);
  await page.keyboard.press("Escape");
  await page.goto(`/sources/${ORG_SOURCE.escalations}`);
  await expect(page.getByTestId("check-result")).toBeVisible();
  await audit(page);
  for (const [button, title] of [
    ["Edit settings", "Edit Halden Escalations"],
    ["Pause", "Pause Halden Escalations"],
    ["Disconnect", "Disconnect Halden Escalations"],
  ] as const) {
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.getByRole("dialog", { name: title })).toBeVisible();
    await audit(page);
    await page.keyboard.press("Escape");
  }
  await page.goto(`/sources/${ORG_SOURCE.handbook}`);
  await expect(page.getByTestId("no-import")).toBeVisible();
  await audit(page);
});

test("keyboard: the upload is reachable and the next step focuses it", async ({ page }) => {
  await openRecord(page, ORG_SOURCE.handbook);
  await page.getByTestId("next-step").getByRole("button", { name: "Choose an export" }).click();
  await expect(page.getByLabel("Export file")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeFocused();
});

for (const [label, width, height] of [
  ["tablet", 768, 1024],
  ["phone", 390, 844],
  ["320 px", 320, 640],
  ["200 % zoom (640 CSS px)", 640, 450],
] as const) {
  test(`${label}: the register, a record and its dialogs reflow without page scroll`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page, { me: ME_LENA, adminSources: REGISTER });
    await page.goto("/sources");
    await expect(page.getByRole("heading", { level: 1, name: "Sources" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.goto(`/sources/${ORG_SOURCE.escalations}`);
    await expect(page.getByTestId("check-result")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.getByRole("button", { name: "Disconnect" }).click();
    const dialog = page.getByRole("dialog", { name: "Disconnect Halden Escalations" });
    await expect(dialog.getByTestId("disconnect-preview")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const box = await dialog.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 0.5).toBe(true);
    await page.keyboard.press("Escape");
    await page.goto(`/sources/${ORG_SOURCE.handbook}`);
    await expect(page.getByTestId("no-import")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("forced colours and reduced motion: margin bars keep their edges, nothing animates", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await openRecord(page, ORG_SOURCE.escalations);
  await expect(page.getByTestId("next-step")).toHaveCSS("border-left-style", "solid");
  await expect(page.getByTestId("check-result")).toBeVisible();
  expect(await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length)).toBe(0);
  expect(SOURCE_RECORD_NEW.next_action).toBe("upload_import");
});
