import { expect, test } from "@playwright/test";

import { ANSWERED, DETAILS, EV, TRACE, TRACE_ASKER, TRACE_HIDDEN } from "../src/test/fixtures";

import { askFor } from "./helpers";
import { mockApi } from "./mock-api";

test("nonexistent and unauthorized evidence look identical", async ({ page }) => {
  // The API answers 404 for both; the UI must not add any difference.
  await mockApi(page);
  const texts: string[] = [];
  for (const id of [EV.missing, "e0000000-0000-4000-8000-0000000abcde", "not-a-uuid"]) {
    await page.goto(`/evidence/${id}`);
    await expect(page.getByRole("heading", { name: "This evidence isn't available" })).toBeVisible();
    texts.push((await page.locator("main").innerText()).trim());
  }
  expect(new Set(texts).size).toBe(1);
});

test("evidence revoked after the answer: the inspector shows only the not-available state", async ({ page }) => {
  const { [EV.decision]: _revoked, ...rest } = DETAILS;
  await askFor(page, ANSWERED, undefined, { evidence: rest });
  await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
  const inspector = page.getByTestId("evidence-inspector");
  await expect(inspector.getByRole("heading", { name: "This evidence isn't available" })).toBeVisible();
  // Nothing from the answer's own copy of that evidence is shown in its place.
  await expect(inspector).not.toContainText("Context: SCIM work was re-scoped");
  await expect(inspector).not.toContainText("Tom Lindqvist");
});

test("a hidden trace answer is never resurrected from the Ask page's memory", async ({ page }) => {
  // The same Ask was just answered in this tab; the trace now says the answer is hidden.
  await askFor(page, { ...ANSWERED, trace_id: TRACE.hidden }, undefined, { traces: { [TRACE.hidden]: TRACE_HIDDEN } });
  await page.getByRole("link", { name: "Trace", exact: true }).click();
  await expect(page.getByText("Answer hidden")).toBeVisible();
  await expect(page.locator("main")).not.toContainText("SCIM general availability to December");
  await expect(page.locator("main")).not.toContainText("Whether Acme has been told");
});

test("traces are fetched fresh on every visit", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto(`/traces/${TRACE.answered}`);
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  await page.goto("/ask");
  await page.goto(`/traces/${TRACE.answered}`);
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  expect(log.traces.filter((id) => id === TRACE_ASKER.trace_id).length).toBeGreaterThanOrEqual(2);
});

test("trace not found and not yours look identical", async ({ page }) => {
  await mockApi(page);
  const texts: string[] = [];
  for (const id of [TRACE.dangling, "70000000-0000-4000-8000-0000000fffff", "garbage"]) {
    await page.goto(`/traces/${id}`);
    await expect(page.getByRole("heading", { name: "This trace isn't available" })).toBeVisible();
    texts.push((await page.locator("main").innerText()).trim());
  }
  expect(new Set(texts).size).toBe(1);
});
