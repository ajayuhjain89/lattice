import { expect, test } from "@playwright/test";

import { FIXTURE_NOW, ID, ME_LENA, TRACE } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

test("members see their own questions by day, each opening its trace", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Activity" }).click();
  await expect(page.getByRole("heading", { name: "Activity", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { level: 3 })).toHaveText(["Today29 Sep 2026 · 1 question", "Yesterday28 Sep 2026 · 1 question"]);
  await expect(page.getByRole("navigation", { name: "Activity" })).toHaveCount(0);
  await page.getByRole("link", { name: "When will SCIM ship for Acme, and what did we tell them?" }).click();
  await expect(page).toHaveURL(new RegExp(`/traces/${TRACE.answered}$`));
  expect(log.traces).toContain(TRACE.answered);
});

test("a question whose answer is now hidden opens the trace's own safe state; Activity shows no answer content", async ({ page }) => {
  await mockApi(page);
  await page.goto("/activity");
  await expect(page.locator("main")).not.toContainText("Kestrel agreed");
  await page.getByRole("link", { name: "What did Kestrel agree to in the renewal?" }).click();
  await expect(page).toHaveURL(new RegExp(`/traces/${TRACE.hidden}$`));
  await expect(page.getByText("Answer hidden")).toBeVisible();
});

test("Ask again puts the question back in the composer, unsent", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto("/activity");
  await page.getByRole("button", { name: "Ask again: What did Kestrel agree to in the renewal?" }).click();
  await expect(page).toHaveURL(/\/ask$/);
  await expect(page.getByRole("textbox", { name: "Question" })).toHaveValue("What did Kestrel agree to in the renewal?");
  expect(log.asks).toEqual([]);
});

test("an empty Activity says what it records", async ({ page }) => {
  await mockApi(page, { activity: { items: [] } });
  await page.goto("/activity");
  await expect(page.getByRole("heading", { name: "You haven't asked anything yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ask a question" })).toBeVisible();
});

test("administrators switch to the organization's runs: who and how, never what", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/activity");
  await page.getByRole("navigation", { name: "Activity" }).getByRole("link", { name: "Organization runs" }).click();
  await expect(page).toHaveURL(/\/activity\/organization$/);
  const table = page.getByRole("table", { name: "Organization runs (operational view)" });
  await expect(table).toContainText("Grace Whitfield");
  await expect(table).toContainText("gemini-3.5-flash-lite");
  await expect(table).not.toContainText("SCIM");
  await expect(page.getByText(/Questions, answers and evidence aren't visible to administrators/)).toBeVisible();
  await expect(table.getByRole("link", { name: "Grace Whitfield", exact: true })).toHaveAttribute("href", `/access/members/${ID.grace}`);
});

test("members reaching the organization's runs get the administrators-only page", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto("/activity/organization");
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
  expect(log.headers.length).toBeGreaterThan(0);
});

for (const width of [...WIDTHS, 320]) {
  test(`activity fits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { me: ME_LENA });
    await page.goto("/activity");
    await expect(page.getByRole("heading", { name: /^Today/ })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.goto("/activity/organization");
    await expect(page.getByRole("table", { name: "Organization runs (operational view)" })).not.toHaveAttribute("aria-busy");
    await expectNoHorizontalOverflow(page);
  });
}
