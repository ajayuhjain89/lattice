import { expect, test, type Page } from "@playwright/test";

import type { components } from "../src/api/schema.gen";
import { FIXTURE_NOW, ME_LENA, SOURCE, SOURCE_FAILED, SOURCES_ADMIN, SOURCES_ADMIN_LONG } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

type Job = components["schemas"]["JobOut"];
const TABLE = "Connected sources and their sync status";

const loaded = async (page: Page, name: string) => {
  const table = page.getByRole("table", { name });
  await expect(table).toBeVisible();
  await expect(table).not.toHaveAttribute("aria-busy");
  return table;
};
const row = (page: Page, name: string) => page.getByRole("table", { name: TABLE }).getByRole("row").filter({ hasText: name });

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

test("members see their own sources and counts, with nothing to change", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Sources" }).click();
  await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
  const table = await loaded(page, "Sources you can read from");
  await expect(table.getByRole("row").filter({ hasText: "Halden Drive" })).toContainText("41 documents");
  // Partial, and the last full sync over a week ago: the time signal, in words.
  await expect(table.getByRole("row").filter({ hasText: "Halden Support" })).toContainText("Last full sync 21 Sep 2026 · 8 days ago");
  await expect(page.locator("main").getByRole("button")).toHaveCount(0);
});

test("the register reads healthy, partial, failed and never-synced sources apart", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, adminSources: { sources: [...SOURCES_ADMIN.sources, SOURCE_FAILED] } });
  await page.goto("/sources");
  await loaded(page, TABLE);
  await expect(page.getByTestId("register-summary")).toHaveText(
    "4 sources: 1 syncing now · 1 whose last sync failed · 1 with failed records · 2 last fully synced over a week ago · 1 never synced.",
  );
  // A failed source is a fault (its own code); failed records are not.
  const wiki = row(page, "Halden Wiki");
  await expect(wiki).toContainText("Failed");
  await expect(wiki).toContainText("source_unavailable:timeout");
  const failedColour = await wiki.getByText("Failed", { exact: true }).evaluate((el) => getComputedStyle(el.closest("span") ?? el).color);
  const support = row(page, "Halden Support");
  await expect(support).toContainText("Partial");
  const partialColour = await support.getByText("Partial", { exact: true }).evaluate((el) => getComputedStyle(el.closest("span") ?? el).color);
  expect(failedColour).not.toBe(partialColour);
  await expect(row(page, "Halden Slack")).toContainText("Never synced");
});

test("Sync now: one request, then queued → running → finished, announced and kept in the row", async ({ page }) => {
  const queued: Job = { ...SOURCES_ADMIN.sources[0]!.job!, id: "7f2a1b0c-9999-4d3e-9f4a-1b2c3d4e5f99", source_id: SOURCE.slack, status: "queued", created_at: "2026-09-29T09:10:00Z", started_at: null };
  let requested = false;
  const slackWith = (job: Job | null, run = SOURCES_ADMIN.sources[2]!.last_run) => ({
    sources: SOURCES_ADMIN.sources.map((s) => (s.id === SOURCE.slack ? { ...s, job, last_run: run } : { ...s, job: null })),
  });
  let readsAtRequest = 0;
  const log = await mockApi(page, {
    me: ME_LENA,
    sync: () => {
      requested = true;
      return { status: 202, body: queued };
    },
    adminSources: (reads) => {
      if (!requested) {
        readsAtRequest = reads;
        return slackWith(null);
      }
      const after = reads - readsAtRequest;
      if (after <= 1) return slackWith(queued);
      if (after === 2) return slackWith({ ...queued, status: "running", started_at: "2026-09-29T09:10:02Z" });
      return slackWith({ ...queued, status: "succeeded", finished_at: "2026-09-29T09:10:30Z" }, { ...SOURCES_ADMIN.sources[0]!.last_run!, finished_at: "2026-09-29T09:10:30Z" });
    },
  });
  await page.goto("/sources");
  await loaded(page, TABLE);
  const button = row(page, "Halden Slack").getByRole("button", { name: "Sync now: Halden Slack" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("sync-announcer")).toHaveText("Sync requested for Halden Slack. It is queued.");
  await expect(row(page, "Halden Slack")).toContainText("Queued since 09:10");
  // Still focused, inert while queued: pressing again sends nothing.
  await expect(button).toBeFocused();
  await expect(button).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("sync-announcer")).toHaveText("Halden Slack is syncing.", { timeout: 10_000 });
  await expect(row(page, "Halden Slack")).toContainText("Syncing since 09:10");
  await expect(page.getByTestId("sync-announcer")).toHaveText("Halden Slack finished syncing: Synced.", { timeout: 10_000 });
  await expect(row(page, "Halden Slack")).toContainText("Synced29 Sep 2026, 09:10");
  await expect(button).not.toHaveAttribute("aria-disabled");
  expect(log.syncs).toEqual([SOURCE.slack]);
  expect(log.headers.some((h) => h["x-lattice-request"] === "1")).toBe(true);
});

test("a refused Sync now stays in the row as an alert", async ({ page }) => {
  await mockApi(page, {
    me: ME_LENA,
    sync: () => ({ status: 503, body: { error: { code: "service_unavailable", message: "Lattice is busy right now.", request_id: "req-503" } } }),
  });
  await page.goto("/sources");
  await loaded(page, TABLE);
  await row(page, "Halden Slack").getByRole("button", { name: "Sync now: Halden Slack" }).click();
  await expect(row(page, "Halden Slack").getByRole("alert")).toHaveText("Couldn't request a sync: Lattice is busy right now. Try again.");
});

test("failed records open inline under their source, by opaque reference", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/sources");
  await loaded(page, TABLE);
  const toggle = page.getByRole("button", { name: "Show failed records: Halden Support (Zendesk)" });
  await toggle.click();
  const records = page.getByRole("table", { name: "Failed records in the last sync of Halden Support (Zendesk)" });
  await expect(records).toContainText("rec_3f9a0c1d2e4b5a67");
  await expect(records).toContainText("opaque reference");
  await expect(page.getByText(/External account ref: 360009913/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Hide failed records: Halden Support (Zendesk)" })).toHaveAttribute("aria-expanded", "true");
});

test("nothing connected is a quiet state that says how sources arrive", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, adminSources: { sources: [] } });
  await page.goto("/sources");
  await expect(page.getByRole("heading", { name: "No sources are connected" })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

for (const width of [...WIDTHS, 320]) {
  test(`sources fit at ${width}px, with long titles, and Sync now stays reachable`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { me: ME_LENA, adminSources: SOURCES_ADMIN_LONG });
    await page.goto("/sources");
    await loaded(page, TABLE);
    await expectNoHorizontalOverflow(page);
    const sync = page.getByRole("button", { name: "Sync now: Halden Slack" });
    await sync.scrollIntoViewIfNeeded();
    await expect(sync).toBeInViewport();
  });
}
